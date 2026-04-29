// The dashboard JavaScript is now organised as ES6 modules under
// `frontend/modules/`. The HTML entry point loads `modules/main.js`
// directly via `<script type="module">`. This file is kept as a
// no-op breadcrumb so any older bookmarked links or cached references
// to `app.js` do not 404; it intentionally does nothing. (There is no
// service worker in this project — only the static file mount in
// app.py serves these assets.)
