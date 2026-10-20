/** Open saved report details without making requests or changing runtime state. */
document.addEventListener('click', (event) => {
  const trigger = event.target.closest('button[data-dialog]');
  if (trigger) document.getElementById(trigger.dataset.dialog).showModal();
});
