---
layer: UI
depends_on: [Core]
red_lines:
  - use tokens
test: swift test --package-path Packages/UI
owns: [Sources]
---
