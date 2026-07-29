# Mnemosyne Memory Tower

Mnemosyne is an opt-in local memory provider:

```yaml
memory:
  provider: mnemosyne
```

It orchestrates existing MyHermes memory layers into one bounded recall block:

- L1 Core from `memories/MEMORY.md` and `memories/USER.md`.
- L2 Episodic facts from the local Holographic Memory store.
- L3 Skill/API state from the local SkillWiki provenance database.
- L4 Reflection/Experience candidates from `memories/mnemosyne/l4.jsonl`.

Mnemosyne is local only. It does not open sockets, call remote services, create
HTTP clients, or provide a default bridge backend. The `MnemosyneBridge`
interface is a protocol boundary for future local integrations only.

L1, L2, and L3 remain owned by their existing systems. Mnemosyne reads Core
memory, Holographic facts, and SkillWiki list/check style state for prefetch,
then renders the merged results through the same injection budget. It does not
rewrite Core memory, update retrieval metadata or trust, install skills, upgrade
skills, execute advisories, or change SkillWiki lifecycle states.

L4 records are candidates only. They can capture local reflections and lessons
under `memories/mnemosyne/l4.jsonl`, but they are not approved facts and are not
promoted into L1 or L2 automatically.

If an optional layer is unavailable or broken, Mnemosyne fails open for that
layer and keeps using the other available layers. Security invariant failures
disable the provider for the session.

Disable or roll back Mnemosyne by changing or removing `memory.provider:
mnemosyne`. This leaves existing L1, L2, and L3 ownership boundaries unchanged.
