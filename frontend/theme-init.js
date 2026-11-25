/* Aplica el tema antes de pintar para evitar el destello inicial.
   Se carga como archivo externo para que la CSP permita `script-src 'self'`. */
(function () {
    try {
        var guardado = localStorage.getItem('sirif_tema');
        var sistema = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
        document.documentElement.dataset.theme =
            guardado === 'light' || guardado === 'dark' ? guardado : sistema;
    } catch (error) {
        document.documentElement.dataset.theme = 'dark';
    }
})();
