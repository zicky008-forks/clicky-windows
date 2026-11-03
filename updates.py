"""GitHub release updates: from Settings on request, or quietly while idle (job 14, Velopack installs only).

Builds installed by Velopack update in place from the latest GitHub release,
downloading only the changed files when a delta package exists. Other builds
fall back to downloading the full installer with digest verification.
"""
import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from storage import data_dir

VERSION='1.6.0-preview'
REPOSITORY='zicky008-forks/clicky-windows'


def velopack_manager():
    """An UpdateManager when this copy was installed by Velopack, else None."""
    try:
        import velopack
        return velopack.UpdateManager(velopack.GithubSource(f'https://github.com/{REPOSITORY}'))
    except Exception:
        return None     # source checkout, Inno Setup install, or no velopack package


def version_key(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z0-9.-]+))?',value)
    if not match:raise ValueError('Unrecognized release version')
    return (*map(int,match.groups()[:3]),0 if match.group(4) else 1)


async def latest():
    manager=velopack_manager()
    if manager is not None:
        info=await asyncio.to_thread(manager.check_for_updates)
        if not info:return None
        target=getattr(info,'TargetFullRelease',None)
        return {'version':str(getattr(target,'Version','') or 'new version'),
                'url':f'https://github.com/{REPOSITORY}/releases/latest','asset':None,'velopack':(manager,info)}
    async with httpx.AsyncClient(timeout=20) as client:
        response=await client.get(f'https://api.github.com/repos/{REPOSITORY}/releases/latest',headers={'Accept':'application/vnd.github+json'})
        response.raise_for_status()
        release=response.json()
    if version_key(release['tag_name'])<=version_key(VERSION):return None
    assets=[asset for asset in release.get('assets',[]) if asset['name'].lower().endswith('.exe')]
    return {'version':release['tag_name'],'url':release['html_url'],'asset':assets[0] if assets else None}


async def apply_in_place(update):
    """Download a Velopack update (delta when available), then restart into it."""
    manager,info=update['velopack']
    await asyncio.to_thread(manager.download_updates,info)
    manager.apply_updates_and_restart(info)


async def download(asset):
    url=asset['browser_download_url']
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname!='github.com' or not parsed.path.startswith('/'+REPOSITORY+'/releases/download/'):
        raise ValueError('Update download is outside the Clicky release repository')
    digest=asset.get('digest','')
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest):
        raise ValueError('This release has no verified SHA-256 digest; use the release page instead')
    name=asset['name']
    if Path(name).name!=name or not name.lower().endswith('.exe'):
        raise ValueError('Invalid installer filename')
    directory=data_dir()/'updates';directory.mkdir(exist_ok=True)
    path=directory/name
    temporary=path.with_suffix('.part')
    hashed=hashlib.sha256();size=0
    try:
        async with httpx.AsyncClient(timeout=120,follow_redirects=True) as client:
            async with client.stream('GET',url) as response:
                response.raise_for_status()
                with temporary.open('wb') as stream:
                    async for chunk in response.aiter_bytes():
                        size+=len(chunk)
                        if size>1_500_000_000:raise ValueError('Update exceeds the size limit')
                        hashed.update(chunk);stream.write(chunk)
        if hashed.hexdigest()!=digest[7:].lower():raise ValueError('Update checksum did not match; installer was not saved')
        temporary.replace(path)
        return str(path)
    finally:temporary.unlink(missing_ok=True)


# ── Quiet updates (job 14) ────────────────────────────────────────────────────
# Velopack installs only. The new version is downloaded while Clicky is idle and applied when
# Clicky exits, so an update never lands while Clicky is working on a file. The next start says
# one line: "Đã cập nhật bản X. Có gì mới: …". Nothing runs in "Chỉ chạy trên máy" mode: the
# check itself would be a connection out of the machine.
PENDING_KEY = 'update_pending'
CHECK_EVERY_S = 6 * 3600
_FALLBACK_NOTE = 'chạy ổn định hơn và sửa vài lỗi nhỏ'


def first_note(markdown):
    """The first plain line of the release notes, short enough for one toast line."""
    for line in (markdown or '').splitlines():
        text = re.sub(r'[*_`>#\[\]]', '', line).strip(' -\t')
        text = re.sub(r'\(https?://[^)]*\)', '', text).strip()
        if text and not text.lower().startswith(('what', 'changes', 'full changelog', 'có gì mới')):
            return text if len(text) <= 120 else text[:117].rstrip() + '…'
    return _FALLBACK_NOTE


async def quiet_check(is_busy, local_only=False, manager=None):
    """Download a new release while idle and hand it to Velopack to apply on exit. Returns the version or None."""
    from storage import set_setting
    if local_only:
        return None
    manager = manager or velopack_manager()
    if manager is None or is_busy():
        return None
    info = await asyncio.to_thread(manager.check_for_updates)
    if not info or getattr(info, 'IsDowngrade', False):
        return None
    target = info.TargetFullRelease
    version = str(getattr(target, 'Version', '') or '')
    if is_busy():
        return None                 # the user started something while we checked: try at the next round
    await asyncio.to_thread(manager.download_updates, info)
    set_setting(PENDING_KEY, {'version': version, 'note': first_note(getattr(target, 'NotesMarkdown', ''))})
    manager.wait_exit_then_apply_updates(info, silent=True, restart=False)
    return version


def after_update_notice(manager=None):
    """The one line shown on the first start after an update was applied, else None."""
    from storage import set_setting, setting
    pending = setting(PENDING_KEY, None)
    if not isinstance(pending, dict) or not pending.get('version'):
        return None
    manager = manager or velopack_manager()
    current = manager.get_current_version() if manager is not None else VERSION
    try:
        applied = version_key(current) >= version_key(pending['version'])
    except ValueError:
        applied = current == pending['version']
    if not applied:
        return None                 # downloaded, not applied yet: say nothing until it is
    set_setting(PENDING_KEY, None)
    return f"Đã cập nhật bản {pending['version']}. Có gì mới: {pending.get('note') or _FALLBACK_NOTE}."
