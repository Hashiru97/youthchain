// Shows the gig-category select only when "Gig / hire-based work" is
// chosen — the category column (Job.category) is only meaningful for
// job_type == "gig" (see app.py's post_job()).
(function () {
  const gigRadio = document.getElementById("job_type_gig");
  const formalRadio = document.getElementById("job_type_formal");
  const categoryField = document.getElementById("gig-category-field");
  if (!gigRadio || !formalRadio || !categoryField) return;

  function sync() {
    categoryField.style.display = gigRadio.checked ? "" : "none";
  }

  gigRadio.addEventListener("change", sync);
  formalRadio.addEventListener("change", sync);
  sync();
})();
