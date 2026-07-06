"""Connection/auth specs and target parsing for SMB and SSH.

SMB target syntax mirrors ``impacket-smbclient``::

    [[domain/]username[:password]@]<host>

plus options: ``hashes`` (LM:NT), ``no_pass``, ``kerberos``, ``aes_key``,
``dc_ip``, ``target_ip``, ``port``.

SSH and FTP target syntax is a URL::

    ssh://[user[:password]@]host[:port][/start_path]
    ftp://[user[:password]@]host[:port][/start_path]     (ftps:// for FTP over TLS)

SSH adds ``identity`` (key file), ``use_agent``, ``known_hosts_policy``. FTP with no
user logs in anonymously.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import unquote, urlsplit


class Proto(str, Enum):
    SMB = "smb"
    SSH = "ssh"
    FTP = "ftp"


# impacket's target parser (impacket.utils.parse_target), inlined so we match
# impacket-smbclient's exact behavior without importing impacket just to parse.
_TARGET_RE = re.compile(r"(?:(?:([^/@:]*)/)?([^@:]*)(?::([^@]*))?@)?(.*)")


def parse_smb_target(target: str) -> tuple[str, str, str, str]:
    """Return ``(domain, username, password, host)`` from an impacket-style target."""
    match = _TARGET_RE.match(target)
    assert match is not None  # the regex matches any string
    domain, username, password, host = match.groups("")
    # A password may itself contain '@'; impacket re-joins it onto the password.
    if "@" in host:
        password = password + "@" + host.rpartition("@")[0]
        host = host.rpartition("@")[2]
    return domain, username, password, host


@dataclass
class SmbAuth:
    host: str
    username: str = ""
    password: str = ""
    domain: str = ""
    lmhash: str = ""
    nthash: str = ""
    aes_key: str = ""
    use_kerberos: bool = False
    no_pass: bool = False
    dc_ip: str | None = None
    target_ip: str | None = None
    port: int = 445

    @property
    def remote_name(self) -> str:
        """The NetBIOS/host name passed to ``SMBConnection(remoteName, ...)``."""
        return self.host

    @property
    def remote_host(self) -> str:
        """The address actually dialed — the explicit target IP if given."""
        return self.target_ip or self.host

    @property
    def has_creds(self) -> bool:
        return bool(self.password or self.nthash or self.lmhash or self.aes_key)


def build_smb_auth(
    target: str,
    *,
    hashes: str | None = None,
    no_pass: bool = False,
    kerberos: bool = False,
    aes_key: str = "",
    dc_ip: str | None = None,
    target_ip: str | None = None,
    port: int = 445,
) -> SmbAuth:
    domain, username, password, host = parse_smb_target(target)
    lmhash = nthash = ""
    if hashes:
        # "LMHASH:NTHASH"; either half may be empty (e.g. ":NTHASH").
        lmhash, _, nthash = hashes.partition(":")
    return SmbAuth(
        host=host,
        username=username,
        password="" if no_pass else password,
        domain=domain,
        lmhash=lmhash,
        nthash=nthash,
        aes_key=aes_key,
        # An AES key implies Kerberos.
        use_kerberos=kerberos or bool(aes_key),
        no_pass=no_pass,
        dc_ip=dc_ip,
        target_ip=target_ip,
        port=port,
    )


def smb_login_kwargs(auth: SmbAuth) -> dict:
    """Kwargs for ``impacket ... SMBConnection.login()``."""
    return dict(
        user=auth.username,
        password=auth.password,
        domain=auth.domain,
        lmhash=auth.lmhash,
        nthash=auth.nthash,
    )


def smb_kerberos_kwargs(auth: SmbAuth) -> dict:
    """Kwargs for ``impacket ... SMBConnection.kerberosLogin()``."""
    return dict(
        user=auth.username,
        password=auth.password,
        domain=auth.domain,
        lmhash=auth.lmhash,
        nthash=auth.nthash,
        aesKey=auth.aes_key,
        kdcHost=auth.dc_ip,
        # Use the ccache (KRB5CCNAME) only when no explicit credential was given.
        useCache=not auth.has_creds,
    )


@dataclass
class SshAuth:
    host: str
    username: str = ""
    password: str = ""
    port: int = 22
    key_filename: str | None = None
    use_agent: bool = True
    start_path: str = "."
    known_hosts_policy: str = "auto"  # 'strict' | 'auto' | 'ignore'


def build_ssh_auth(
    target: str,
    *,
    identity: str | None = None,
    use_agent: bool = True,
    known_hosts_policy: str = "auto",
) -> SshAuth:
    if "://" not in target:
        target = "ssh://" + target
    parts = urlsplit(target)
    if parts.scheme != "ssh":
        raise ValueError(f"not an ssh target: {target!r}")
    if not parts.hostname:
        raise ValueError("ssh target requires a host")
    return SshAuth(
        host=parts.hostname,
        username=unquote(parts.username) if parts.username else "",
        password=unquote(parts.password) if parts.password else "",
        port=parts.port or 22,
        key_filename=identity,
        use_agent=use_agent,
        start_path=parts.path or ".",
        known_hosts_policy=known_hosts_policy,
    )


@dataclass
class FtpAuth:
    host: str
    username: str = ""  # empty -> anonymous
    password: str = ""
    port: int = 21
    use_tls: bool = False  # ftps:// -> FTP over TLS (FTP_TLS + PROT P)
    start_path: str = "."


def build_ftp_auth(target: str) -> FtpAuth:
    if "://" not in target:
        target = "ftp://" + target
    parts = urlsplit(target)
    if parts.scheme not in ("ftp", "ftps"):
        raise ValueError(f"not an ftp target: {target!r}")
    if not parts.hostname:
        raise ValueError("ftp target requires a host")
    return FtpAuth(
        host=parts.hostname,
        username=unquote(parts.username) if parts.username else "",
        password=unquote(parts.password) if parts.password else "",
        port=parts.port or 21,
        use_tls=(parts.scheme == "ftps"),
        start_path=parts.path or ".",
    )


@dataclass
class ConnSpec:
    proto: Proto
    smb: SmbAuth | None = None
    ssh: SshAuth | None = None
    ftp: FtpAuth | None = None


def make_conn_spec(target: str, **options) -> ConnSpec:
    """Dispatch on scheme: ``ssh://`` → SSH, ``ftp(s)://`` → FTP, else impacket SMB."""
    if target.startswith("ssh://"):
        return ConnSpec(
            Proto.SSH,
            ssh=build_ssh_auth(
                target,
                identity=options.get("identity"),
                use_agent=options.get("use_agent", True),
                known_hosts_policy=options.get("known_hosts_policy", "auto"),
            ),
        )
    if target.startswith(("ftp://", "ftps://")):
        return ConnSpec(Proto.FTP, ftp=build_ftp_auth(target))
    return ConnSpec(
        Proto.SMB,
        smb=build_smb_auth(
            target,
            hashes=options.get("hashes"),
            no_pass=options.get("no_pass", False),
            kerberos=options.get("kerberos", False),
            aes_key=options.get("aes_key", ""),
            dc_ip=options.get("dc_ip"),
            target_ip=options.get("target_ip"),
            port=options.get("port", 445),
        ),
    )
