(() => {
  const landing = document.querySelector('.landing');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let leaving = false;
  let exitAnimation;

  // Restore the page when returning through the browser's back/forward cache.
  window.addEventListener('pageshow', () => {
    leaving = false;
    exitAnimation?.cancel();
  });

  document.querySelectorAll('.start-button, .header-link').forEach((link) => {
    link.addEventListener('click', (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey ||
          event.ctrlKey || event.shiftKey || event.altKey ||
          reducedMotion.matches || !landing?.animate) return;
      event.preventDefault();
      if (leaving) return;
      leaving = true;

      exitAnimation = landing.animate([
        { opacity: 1, transform: 'translateY(0) scale(1)' },
        { opacity: 0, transform: 'translateY(-14px) scale(.985)' },
      ], { duration: 280, easing: 'cubic-bezier(.4, 0, 1, 1)', fill: 'forwards' });

      // Navigation also completes if the animation is cancelled unexpectedly.
      exitAnimation.finished.catch(() => {}).then(() => {
        if (leaving) window.location.assign(link.href);
      });
    });
  });
})();
