// Swaps the document-upload label/help text between "business document"
// and "personal ID" based on which verification track is selected -- see
// app.py's employer_verification() and Employer.verification_type.
(function () {
  const businessRadio = document.getElementById("verification_type_business");
  const individualRadio = document.getElementById("verification_type_individual");
  const label = document.getElementById("document_label");
  const help = document.getElementById("document_help");
  if (!businessRadio || !individualRadio || !label || !help) return;

  function sync() {
    if (individualRadio.checked) {
      label.textContent = "Personal ID";
      help.textContent = "National ID, voter's card, or driver's license.";
    } else {
      label.textContent = "Business document";
      help.textContent = "Business registration certificate, tax ID, or similar.";
    }
  }

  businessRadio.addEventListener("change", sync);
  individualRadio.addEventListener("change", sync);
  sync();
})();
