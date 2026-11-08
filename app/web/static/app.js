// Errori delle richieste HTMX: senza questo un 5xx, un timeout o la rete assente non mostrano nulla
(function () {
  function messaggio(stato) {
    if (stato === 413) return "Il file e' troppo grande.";
    if (stato === 502 || stato === 503 || stato === 504)
      return "Il server non ha risposto in tempo. Riprovare tra poco; se si tratta di una lettura, " +
        "provare con una foto piu' piccola o compilare i campi a mano.";
    if (stato === 400) return "Richiesta non valida. Ricaricare la pagina e riprovare.";
    if (stato) return "Errore del server (" + stato + "). Riprovare; se si ripete, avvisare l'assistenza.";
    return "Connessione assente o interrotta. Controllare la rete e riprovare.";
  }
  function mostra(testo) {
    var box = document.getElementById("errore-richiesta");
    if (!box) return;
    box.textContent = testo;
    box.hidden = false;
    box.scrollIntoView({ block: "nearest" });
  }
  document.addEventListener("htmx:responseError", function (e) {
    mostra(messaggio(e.detail.xhr && e.detail.xhr.status));
  });
  document.addEventListener("htmx:sendError", function () { mostra(messaggio(0)); });
  document.addEventListener("htmx:timeout", function () { mostra(messaggio(504)); });
  document.addEventListener("htmx:beforeRequest", function () {
    var box = document.getElementById("errore-richiesta");
    if (box) box.hidden = true;
  });
})();
