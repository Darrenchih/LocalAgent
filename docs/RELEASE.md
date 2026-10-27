# Release baseline 0.37.0

The historical REVIEW-044S candidate is a Windows Frozen --no-bundle development build, not an Installer. Its original executable hashes will be freshly checked in migration records. Original private binaries are preserved locally outside the project cleanup target.

**Public Frozen upload is withheld pending payload privacy and redistribution clearance.** The historical dependency review reports 777 dependency entries, 12 missing license texts and 246 unknown license metadata entries. It is a conservative lockfile superset, not an exact shipped dependency closure. Rust diagnostic strings in the historical APP expose its local build profile path; notice files expose local cache paths. These are not fixed merely by opening an Issue. Private source/build/audit originals are preserved; no original secret is intentionally published and later removed from Git.

The source baseline can be inspected and recovered independently. A Release must not be described as a usable Windows app until its actual cleared Frozen assets are available and anonymously downloaded/hash-verified. No placeholder Release is used to claim completion. These blockers are tracked in Issues. Future cleared artifacts must use a pre-release/development tag based on actual 0.37.0 and disclose any sanitization/rebuild hash changes.

GitHub documented limits checked for migration: normal Git blocks individual files over 100 MiB; each Release asset must be under 2 GiB. Source excludes binary payloads rather than using Git history as binary storage.

References: https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github and https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
