// Open a robot section when the URL hash is its id (e.g. #unitree-g1).
// Truncate the scenario-dot strip to one line and show +n when dots don't fit.
(function () {
  function openFromHash() {
    const id = decodeURIComponent((location.hash || "").replace(/^#/, ""));
    if (!id) return;
    const el = document.getElementById(id);
    if (el && el.tagName === "DETAILS") {
      el.open = true;
      el.scrollIntoView({ block: "start" });
    }
  }

  function setHashFor(details) {
    if (!details.id) return;
    const next = details.open ? "#" + details.id : " ";
    history.replaceState(null, "", next === " " ? location.pathname + location.search : next);
  }

  function fitDots(strip) {
    const more = strip.querySelector(".pip-more");
    const pips = [...strip.querySelectorAll(".pip")];
    pips.forEach((p) => { p.hidden = false; });
    if (more) {
      more.hidden = true;
      more.textContent = "";
    }
    const fits = () => strip.scrollWidth <= strip.clientWidth + 1;
    if (fits()) return;
    if (!more) return;
    more.hidden = false;
    let hidden = 0;
    for (let i = pips.length - 1; i >= 1; i--) {
      pips[i].hidden = true;
      hidden += 1;
      more.textContent = "+" + hidden;
      if (fits()) return;
    }
  }

  function fitAll() {
    document.querySelectorAll(".dots").forEach(fitDots);
  }

  document.querySelectorAll("details.robot").forEach((d) => {
    d.addEventListener("toggle", () => {
      // Only rewrite the hash when this section is the one being opened,
      // or when the open hash pointed at the section just closed.
      if (d.open || location.hash === "#" + d.id) setHashFor(d);
    });
  });

  function start() {
    openFromHash();
    fitAll();
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(fitAll);
    }
  }
  window.addEventListener("hashchange", openFromHash);
  window.addEventListener("resize", fitAll);
  window.addEventListener("load", fitAll);
  document.addEventListener("DOMContentLoaded", start);
  if (document.readyState !== "loading") start();
})();
