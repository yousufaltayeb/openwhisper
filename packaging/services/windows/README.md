# Windows service identity

The signed MSIX registers the hidden desktop identity and per-user startup task
for `io.github.yousufaltayeb.openwhisper`. Portable ZIP mode supports foreground
and file commands until `openwhisper setup` performs the same idempotent
registration. The named pipe must be created with a DACL restricted to the
current user SID before this package can leave alpha.
