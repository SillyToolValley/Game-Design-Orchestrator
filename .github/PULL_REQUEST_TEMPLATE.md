## Summary

Describe the designer problem and the smallest useful behavior change.

## Human-led workflow impact

Explain how this helps a person discover options, challenge assumptions with references, make a consequential choice, or avoid repetitive work. If none apply, state the concrete usability, portability, or filesystem-safety benefit.

Could this change let the AI silently approve its own recommendation, present inference as fact, or create unnecessary paperwork? Explain the safeguard.

## Behavior and compatibility

- Public CLI affected: yes / no
- Lean artifact format affected: yes / no
- Existing projects affected: yes / no
- Migration needed: yes / no

Describe user-visible changes and migration behavior.

## Checks

List commands and platforms tested, including focused success and failure cases.

```text
python -m compileall -q skills/orchestrate-game-design/scripts
python -m unittest discover -s skills/orchestrate-game-design/tests -p 'test_*.py' -v
```

## Checklist

- [ ] I followed `CONTRIBUTING.md` and kept the change focused.
- [ ] Behavioral changes have focused regression tests.
- [ ] Public examples, help text, templates, and changelog are updated where needed.
- [ ] New fixtures contain no real player data, credentials, or proprietary game content.
- [ ] The human still owns consequential creative choices.
- [ ] References distinguish sourced facts from inference and transfer limits.
- [ ] Diagnostics do not pretend to rate design quality.
