# Profile configuration UI ownership

Every persisted `SupplierProfile` property must have a non-code workflow. The
executable inventory lives in `stencil.profiles.ui_coverage`; its test fails
when a new property is introduced without an owner.

- `profile_editor`: user-owned configuration editable in the profile form,
  including identity, schema/spec selection, extraction fields, output mapping,
  fixed values, advanced extraction hints, fingerprinting, and training gates.
- `other_ui_workflow`: account delivery mappings, managed in the Accounts view.
- `generated_read_only`: extraction plans, authoring evidence, audit metadata,
  and timestamps. These are visible in the editor but cannot be manually changed.

JSON import/export remains an administrative compatibility tool. It is not the
required path for configuring any user-owned profile behavior.
