# Releasing Clicky BI

A `v*` tag runs `.github/workflows/build.yml`. Its BI job packs the build with Velopack and publishes a GitHub
Release. Installed copies download that release quietly while idle (`updates.quiet_check`). They never check in
"Chỉ chạy trên máy" mode, so robot runs with `CLICKY_LOCAL_ONLY=1` stay on their build.

## Rules

- **Tag exactly the commit the robot passed.** That means Case 01 `--lan 3` plus the hidden Case 02–05 with 0 false
  alarms, all on that commit's installer.
- **The tag version must be above every installed CI build.** CI builds are `<VERSION in updates.py>.ciN`. After a
  release, raise `VERSION` to the next patch with `-preview`, so that later CI builds sort above the release.
  Otherwise a quiet update would replace a newer test build with the older release.
- **Release notes live in `docs/phat-hanh/<x.y.z>.md` and are written in plain Vietnamese.**
  - The first line is the sentence Clicky says after a quiet update: "Đã cập nhật bản X. Có gì mới: <line>". Keep it
    under 120 characters.
  - The workflow embeds the file with `vpk pack --releaseNotes` when the file exists at the tagged commit.
  - If the tagged commit predates the file (v1.6.0 does), the in-app line falls back to a generic sentence. In that
    case, set the file as the release body on GitHub after the workflow publishes the release.

## Steps

```bash
git fetch origin
git tag -a vX.Y.Z <tested-commit> -m "Clicky BI X.Y.Z"
git push origin vX.Y.Z
# wait for "Windows installer build" on the tag to pass, then (only if the notes were not embedded):
gh api repos/zicky008-forks/clicky-windows/releases/tags/vX.Y.Z --jq .id
gh api -X PATCH repos/zicky008-forks/clicky-windows/releases/<id> -F body=@docs/phat-hanh/X.Y.Z.md
```

Then check the release: it should have a `-Setup.exe`, a full `.nupkg` and `releases.win.json`, and it should
not be marked as a pre-release.
