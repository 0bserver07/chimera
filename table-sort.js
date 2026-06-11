// Progressive enhancement: click-to-sort for field-guide tables.
// Loaded site-wide (like mermaid-init.js); activates only on /field-guide/ pages.
if (location.pathname.includes('/field-guide/')) {
  document.querySelectorAll('.sl-markdown-content table').forEach((table) => {
    const tbody = table.querySelector('tbody');
    if (!tbody || tbody.rows.length < 2) return;
    table.querySelectorAll('thead th').forEach((th, idx) => {
      th.style.cursor = 'pointer';
      th.title = 'Click to sort';
      th.addEventListener('click', () => {
        const dir = th.dataset.dir === 'asc' ? -1 : 1;
        table.querySelectorAll('thead th').forEach((h) => delete h.dataset.dir);
        th.dataset.dir = dir === 1 ? 'asc' : 'desc';
        Array.from(tbody.rows)
          .sort((a, b) => {
            const x = a.cells[idx]?.textContent.trim() ?? '';
            const y = b.cells[idx]?.textContent.trim() ?? '';
            const nx = parseFloat(x);
            const ny = parseFloat(y);
            const cmp = Number.isNaN(nx) || Number.isNaN(ny) ? x.localeCompare(y) : nx - ny;
            return cmp * dir;
          })
          .forEach((row) => tbody.appendChild(row));
      });
    });
  });
}
