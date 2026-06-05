// Swap Columns 3 (Description) and 4 (Default) in 4-column API tables
(function () {
  const swapColumns = () => {
    const tables = document.querySelectorAll(".md-typeset .doc-contents table");
    tables.forEach(table => {
      const headers = table.querySelectorAll("thead tr th");
      if (headers.length === 4 && headers[3].textContent.trim().toLowerCase() === "default") {
        const trs = table.querySelectorAll("tr");
        trs.forEach(tr => {
          const cells = tr.children;
          if (cells.length === 4) {
            // Swap cell at index 2 (Description) and cell at index 3 (Default)
            // By inserting cell 3 (Default) before cell 2 (Description)
            tr.insertBefore(cells[3], cells[2]);
          }
        });
      }
    });
  };

  // Run on load
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", swapColumns);
  } else {
    swapColumns();
  }

  // Support MkDocs Material instant loading
  if (typeof document$ !== "undefined" && typeof document$.subscribe === "function") {
    document$.subscribe(() => {
      swapColumns();
    });
  }
})();
