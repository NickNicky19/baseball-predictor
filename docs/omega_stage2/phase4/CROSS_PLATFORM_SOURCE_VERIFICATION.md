# Cross-Platform Source Verification

Schema version: `omega-cross-platform-source-verification-v1`.

On Windows, the loader combines reparse-point inspection with non-mutating `CreateFileW` access probes for write-data/add-file, append/add-subdirectory, delete, DACL change, and ownership change. A granted capability rejects the tree; errors other than access-denied or sharing-violation are inconclusive and reject it.

On POSIX, it combines regular-file/path checks, writable mode-bit rejection, and effective-ID `os.access` evaluation without following symlinks. Effective access catches ACL-based permission that mode bits alone may not reveal. Unsupported effective-ID or no-follow evaluation is inconclusive and rejects the tree.

Windows tests executed locally. POSIX behavior is implemented and covered by platform-conditional tests, but the required clean Ubuntu and Linux-container executions are pending because neither Docker nor WSL is available on this host. No deployed filesystem claim is made.
