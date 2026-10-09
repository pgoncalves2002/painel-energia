// Apoio às telas: temporizadores que param ao sair da tela e proteção contra respostas atrasadas.

export function scope() {
  const timers = [];
  const cleanups = [];
  let alive = true;
  const seqs = new Map();
  return {
    get alive() {
      return alive;
    },
    /** Repete fn a cada ms enquanto a tela estiver aberta e a aba visível. */
    every(ms, fn) {
      timers.push(setInterval(() => {
        if (alive && !document.hidden) fn();
      }, ms));
    },
    onCleanup(fn) {
      cleanups.push(fn);
    },
    /**
     * Executa a busca e só aplica o resultado se a tela ainda estiver aberta e nenhuma
     * busca mais nova do mesmo tipo (key) tiver sido iniciada.
     */
    async load(key, fetcher, apply, onError) {
      const my = (seqs.get(key) || 0) + 1;
      seqs.set(key, my);
      try {
        const data = await fetcher();
        if (alive && seqs.get(key) === my) apply(data);
      } catch (e) {
        if (alive && seqs.get(key) === my) {
          if (onError) onError(e);
          else console.error(e);
        }
      }
    },
    destroy() {
      alive = false;
      for (const t of timers) clearInterval(t);
      for (const fn of cleanups) {
        try {
          fn();
        } catch (e) {
          console.error(e);
        }
      }
    },
  };
}
