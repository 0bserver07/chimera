async function renderMermaid() {
  const pres = document.querySelectorAll('pre[data-language="mermaid"]');
  if (pres.length === 0) return;

  const { default: mermaid } = await import(
    "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"
  );

  mermaid.initialize({
    startOnLoad: false,
    theme: "dark",
    themeVariables: {
      darkMode: true,
      background: "transparent",
      fontFamily: "Inter, system-ui, sans-serif",
      fontSize: "14px",

      // Node colors — dark bg, white text
      primaryColor: "#352b5e",
      primaryTextColor: "#ffffff",
      primaryBorderColor: "#7c3aed",
      secondaryColor: "#1e3a2a",
      secondaryTextColor: "#ffffff",
      secondaryBorderColor: "#22c55e",
      tertiaryColor: "#3b2e20",
      tertiaryTextColor: "#ffffff",

      // Edges
      lineColor: "#a78bfa",

      // All text
      textColor: "#ffffff",
      nodeTextColor: "#ffffff",
      labelTextColor: "#ffffff",

      // Main node background
      mainBkg: "#352b5e",
      nodeBorder: "#7c3aed",

      // Subgraph clusters
      clusterBkg: "#1a1714",
      clusterBorder: "#4a453e",
      titleColor: "#e0d4fc",

      edgeLabelBackground: "#221e1a",

      // Notes
      noteBkgColor: "#352b5e",
      noteTextColor: "#ffffff",
      noteBorderColor: "#7c3aed",

      // Flowchart specific
      nodeBkg: "#352b5e",
    },
  });

  pres.forEach((pre) => {
    const lines = pre.querySelectorAll(".ec-line");
    const code =
      lines.length > 0
        ? Array.from(lines)
            .map((l) => l.textContent)
            .join(String.fromCharCode(10))
        : pre.textContent;

    const ecWrapper =
      pre.closest(".expressive-code") ||
      pre.closest("figure") ||
      pre.parentElement;

    const container = document.createElement("div");
    container.className = "mermaid-container";

    const controls = document.createElement("div");
    controls.className = "mermaid-controls";
    controls.innerHTML =
      '<button class="mermaid-btn" data-action="zoom-in">+</button>' +
      '<button class="mermaid-btn" data-action="zoom-out">\u2212</button>' +
      '<button class="mermaid-btn" data-action="reset">Reset</button>';

    const viewport = document.createElement("div");
    viewport.className = "mermaid-viewport";

    const diagram = document.createElement("div");
    diagram.className = "mermaid";
    diagram.textContent = code;

    viewport.appendChild(diagram);
    container.appendChild(controls);
    container.appendChild(viewport);
    ecWrapper.replaceWith(container);
  });

  await mermaid.run({ querySelector: ".mermaid" });

  // Fix edges and arrows to be visible on dark bg (don't touch node colors — classDef handles those)
  document.querySelectorAll(".mermaid .edgePath path, .mermaid .flowchart-link").forEach((el) => {
    el.style.stroke = "#a78bfa";
  });
  document.querySelectorAll(".mermaid marker path").forEach((el) => {
    el.style.fill = "#a78bfa";
    el.style.stroke = "#a78bfa";
  });
  document.querySelectorAll(".mermaid .edgeLabel").forEach((el) => {
    el.style.background = "#221e1a";
    el.style.color = "#e0d4fc";
  });
  document.querySelectorAll(".mermaid .cluster rect").forEach((el) => {
    el.style.fill = "#1a1714";
    el.style.stroke = "#4a453e";
  });
  document.querySelectorAll(".mermaid .cluster .nodeLabel").forEach((el) => {
    el.style.fill = "#e0d4fc";
  });

  // Wire up zoom + drag-to-pan
  document.querySelectorAll(".mermaid-container").forEach((container) => {
    let scale = 1;
    const viewport = container.querySelector(".mermaid-viewport");
    const diagram = container.querySelector(".mermaid");

    // Zoom buttons
    container.querySelectorAll(".mermaid-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const action = btn.dataset.action;
        if (action === "zoom-in") scale = Math.min(scale + 0.25, 4);
        else if (action === "zoom-out") scale = Math.max(scale - 0.25, 0.25);
        else if (action === "reset") scale = 1;
        diagram.style.transform = "scale(" + scale + ")";
        diagram.style.transformOrigin = "top left";
      });
    });

    // Drag to pan
    let isDragging = false;
    let startX, startY, scrollLeft, scrollTop;

    viewport.addEventListener("mousedown", (e) => {
      isDragging = true;
      startX = e.pageX - viewport.offsetLeft;
      startY = e.pageY - viewport.offsetTop;
      scrollLeft = viewport.scrollLeft;
      scrollTop = viewport.scrollTop;
    });

    viewport.addEventListener("mouseleave", () => { isDragging = false; });
    viewport.addEventListener("mouseup", () => { isDragging = false; });

    viewport.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      e.preventDefault();
      const x = e.pageX - viewport.offsetLeft;
      const y = e.pageY - viewport.offsetTop;
      viewport.scrollLeft = scrollLeft - (x - startX);
      viewport.scrollTop = scrollTop - (y - startY);
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderMermaid);
} else {
  renderMermaid();
}
