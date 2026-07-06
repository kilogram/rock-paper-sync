"""Layout testbench CLI (Layer C of docs/DEVICE_REPLICA_DESIGN.md).

Composes the production parser, RemarkableGenerator, layout package, and
the tools/rmlib RmRenderer into a local render/diff/preview loop:

    uv run python -m rock_paper_sync.bench render doc.md [-o outdir] [--overlay]
    uv run python -m rock_paper_sync.bench diff a.png b.png [-o out.png]
    uv run python -m rock_paper_sync.bench preview <vault_dir> <doc.md>

The bench owns NO layout math: every layout quantity comes from
DeviceGeometry / LayoutContext / WordWrapLayoutEngine. Constants defined
here are presentation-only (overlay colors and alpha).
"""
