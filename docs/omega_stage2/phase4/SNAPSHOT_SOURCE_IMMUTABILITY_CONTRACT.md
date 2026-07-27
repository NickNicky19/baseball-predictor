# Snapshot Source Immutability Contract

Schema version: `omega-snapshot-source-immutability-contract-v1`.

The manifest and snapshot must be non-empty regular files under a required immutability anchor. Relative POSIX paths only are accepted. Every path component is checked with `lstat`; symlinks, junctions, reparse points, escapes, writable parents, writable files, and unsupported permission states fail closed.

The reader validates path identity, opens read-only with no-follow where available, compares pre-open `lstat` with descriptor `fstat`, reads and hashes through that descriptor, compares descriptor size and modification time after the read, rechecks path identity, and revalidates the chain. Size ceilings are required external operational settings.

The loader never changes source permissions or writes a probe file. This contract proves only the local checks actually executed; deployed storage immutability remains QA-007/QA-008 work.
