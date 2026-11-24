# Assignment 1: `for` and `do`/`while` by desugaring

**Group:** _<names / student IDs>_

## Solution

We added both loops **in the front end only**, turning them into constructs Eta already has: `while`, blocks, local declarations and assignments. Name resolution, type checking, WTAC generation, the optimiser and codegen are **unchanged**. They only ever see an AST that has no `for` or `do`.

| File | Change |
|---|---|
| `rwetac/src/token.rs` | Two new keyword tokens: `Token::Do` (`"do"`) and `Token::For` (`"for"`). |
| `rwetac/src/parser.rs` | `parse_statement` sends `do` to `parse_do_while` and `for` to `parse_for`. Added `parse_assignment_no_semi`, a copy of the assignment/declaration branch of `parse_statement` that does not expect a trailing `;`, so it can parse the step clause of `for`, which is followed by `{`. |

**`for`.** `for init; guard; step body` is rewritten to

```
{ init; while guard { body; step } }
```

The outer block keeps the loop variable local to the loop. So `for i: int = ...` can be written twice in a row, and the variable can't leak or clash with an `i` declared after the loop. The init clause goes through the normal `parse_statement`, so it can also be a plain assignment to a variable that already exists (`for k = 10; ...`). The body is still its own block inside the `while`, so variables declared in the body are not visible to `step`.

**`do`/`while`.** `do { body } while e` is rewritten to

```
{ #go: bool = true; while #go { body; #go = e } }
```

The hidden flag makes the body run once before the guard is checked. Its name starts with `#`, which the lexer never produces in an identifier, so it can't collide with a user variable. Each `do` gets its own outer block, so nested `do/while` loops each have their own `#go` and don't shadow each other. `e` is evaluated at the end of the body block, so the guard can use variables declared in the body, e.g. `r` in `04_do_while_gcd.eta`. The `;` that the lexer inserts after the closing `}` (when `while` is on the next line) is consumed if present. Since `(e)` is just a parenthesised expression, both `while (e)` and `while e` work.

The synthesised nodes reuse the span of the `for`/`do` keyword, so errors raised later, in the type checker for example, point at the loop.

## Examples (`a1/examples/`)

Each file has an `// EXPECT:` header in the format of `runtime/tests/integration_tests/`. All six give the expected result with and without `--opt` (`a1/run_examples.sh`), and the existing test suite still passes.

| Program | Shows | Result |
|---|---|---|
| `01_for_sum` | basic `for` | 5050 |
| `02_do_while_digits` | body runs at least once (`digits(0) = 1`) | 51 |
| `03_for_bubble_sort` | nested `for` over an array, inner bound depends on outer variable | 12345 |
| `04_do_while_gcd` | guard reads a variable declared in the body | 6 |
| `05_nested_mixed` | `for`/`do` mixed and nested, two nested `do/while`s | 4 |
| `06_scoping` | reusing `i`, init that is an assignment, zero-iteration `for`, `do … while (false)` | 81 |

## Lessons learned

- **Desugaring is cheap.** About 170 lines in the parser gave two new loops that work with every later phase, including the optimisation passes, without touching them.
- **Scope is the subtle part.** Our first `for` rewrite put the declaration of `i` directly into the enclosing block, so two loops using `i` in the same function failed as a redeclaration. Wrapping everything in a fresh block fixed it.
- **Fresh names need a namespace the user can't reach.** A name like `go` could clash with user code. Using a character the lexer rejects (`#`) makes the hidden variable safe for free.
- **Automatic semicolon insertion matters.** The lexer inserts `;` after `}` before a newline, so the parser has to accept an optional `;` between `}` and `while`. It also explains why a one-line body such as `{ s = s + 1 }` is rejected: that is an existing limitation that plain `while` has too, not something we introduced.
- **Errors must be propagated.** At first `parse_for` called `expect(Token::Semicolon)` without `?`. The result was dropped, so a missing `;` in the header gave a confusing error later on (`Unexpected Token Equal`). With `?` it now reports `Expected Semicolon` at the right place.
- **Desugaring depends on the rest of the language.** The `for` rewrite is correct only because Eta has no `continue`. With `continue`, the step would be skipped. A `break`/`continue` extension would need a real loop node or labelled jumps.
