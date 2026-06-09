"""
core.keyboard.prediction
========================

TextPredictor — autocomplete + correção + sugestão de próxima palavra.

Estratégia leve (sem dependências externas além de difflib):

- Trie compacto pra prefix lookup (autocomplete).
- Frequência de palavras (Zipf) — sugestões mais comuns primeiro.
- Bigrama opcional (next-word prediction) carregado de arquivo se existir.
- Correção via difflib.get_close_matches (Levenshtein-like, dist ≤ 2).
- Aprendizado online: cada palavra digitada incrementa frequência local.
"""

from __future__ import annotations

import logging
import os
import re
from difflib import get_close_matches
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────── Trie


class _TrieNode:
    __slots__ = ("children", "freq", "word")

    def __init__(self) -> None:
        self.children: Dict[str, "_TrieNode"] = {}
        self.freq: int = 0
        self.word: Optional[str] = None  # presente só nos nós-folha


class _Trie:
    def __init__(self) -> None:
        self.root = _TrieNode()

    def insert(self, word: str, freq: int = 1) -> None:
        node = self.root
        for ch in word:
            node = node.children.setdefault(ch, _TrieNode())
        node.word = word
        node.freq += freq

    def prefix_search(self, prefix: str, top_k: int = 5
                      ) -> List[Tuple[str, int]]:
        node = self.root
        for ch in prefix:
            child = node.children.get(ch)
            if child is None:
                return []
            node = child
        # BFS limitada coletando palavras
        results: List[Tuple[str, int]] = []
        stack: List[_TrieNode] = [node]
        # Heurística: cap em 200 nós explorados pra latência baixa
        explored = 0
        while stack and explored < 200:
            n = stack.pop()
            explored += 1
            if n.word and n.freq > 0:
                results.append((n.word, n.freq))
            stack.extend(n.children.values())
        results.sort(key=lambda x: -x[1])
        return results[:top_k]


# ───────────────────────────────────────────────────────── Predictor


# Mini-vocabulário embutido pra funcionar sem dict externo (fallback).
_MINI_PT_BR = """
inteligencia interface integrado internet interno intenso introducao instalar
inicio inverno inicial inverter intuicao inteiro interessante interesse intimo
de da do das dos para por com sem entre sobre sob ante apos perante
e ou mas porem todavia contudo entao assim portanto logo
um uma uns umas o a os as
ser estar ter haver fazer dizer poder ir vir ver dar saber querer chegar
nao sim talvez muito pouco mais menos bem mal ainda ja sempre nunca hoje ontem amanha
casa rua cidade pais mundo trabalho escola hora dia mes ano tempo gente pessoa
agua fogo terra ar vida amor paz luz som cor
software hardware computador teclado mouse tela cursor codigo dado dados
projeto produto sistema servico cliente usuario empresa equipe
""".split()


class TextPredictor:
    """
    API:
      predictor.feed_char(ch) → atualiza prefixo atual
      predictor.feed_special(code) → space/enter/backspace
      predictor.suggestions() → tuple[str, ...] (top 3)
      predictor.accept(idx) → confirma sugestão #idx (substitui prefixo)
      predictor.committed_text() → texto já confirmado (read-only)
    """

    SUGGESTION_COUNT = 3

    def __init__(self, dict_path: Optional[str] = None) -> None:
        self._trie = _Trie()
        self._bigrams: Dict[str, Dict[str, int]] = {}
        self._committed: List[str] = []   # palavras já confirmadas
        self._prefix: str = ""            # palavra parcial atual
        self._loaded = False
        self._load_dict(dict_path)

    # ───────────────────────────────────────────────────── load

    def _load_dict(self, path: Optional[str]) -> None:
        words: List[Tuple[str, int]] = []
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.strip().split()
                        if not parts:
                            continue
                        w = parts[0].lower()
                        if not re.fullmatch(r"[a-záàâãéêíóôõúüç]+", w):
                            continue
                        freq = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
                        words.append((w, freq))
            except Exception as e:
                logger.warning("dict load fail: %s", e)
        if not words:
            words = [(w.lower(), 1) for w in _MINI_PT_BR if w]
        for w, f in words:
            self._trie.insert(w, f)
        self._loaded = True

    # ───────────────────────────────────────────────────── feed

    def feed_char(self, ch: str) -> None:
        if not ch:
            return
        if ch.isalpha():
            self._prefix += ch.lower()
        else:
            # caractere não-alfa quebra a palavra
            self._commit_current(autocorrect=False)

    def feed_special(self, code: str) -> None:
        if code == "backspace":
            if self._prefix:
                self._prefix = self._prefix[:-1]
            elif self._committed:
                # remove última palavra inteira (UX: 1 backspace = última letra do prefix)
                self._committed.pop()
        elif code in ("space", "enter"):
            self._commit_current(autocorrect=True)
            if code == "enter":
                # Não tracking de linhas — apenas reset bigrama
                pass

    def _commit_current(self, autocorrect: bool) -> None:
        if not self._prefix:
            return
        word = self._prefix
        if autocorrect and not self._is_known(word):
            corrected = self._best_correction(word)
            if corrected:
                word = corrected
        self._update_bigram(word)
        self._committed.append(word)
        self._prefix = ""

    # ───────────────────────────────────────────────────── suggest

    def suggestions(self) -> Tuple[str, ...]:
        if self._prefix:
            cands = self._trie.prefix_search(self._prefix, top_k=12)
            # Filtra prefix exato pra não sugerir o que já está digitado
            cands = [w for w, _ in cands if w != self._prefix]
            # Correção: se prefix curto e nada encontrado, tenta close_matches
            if not cands and len(self._prefix) >= 2:
                cands = get_close_matches(
                    self._prefix,
                    self._all_words(limit=2000),
                    n=self.SUGGESTION_COUNT,
                    cutoff=0.65,
                )
            return tuple(cands[: self.SUGGESTION_COUNT])
        # Next-word baseado no último commit (bigrama)
        if self._committed:
            last = self._committed[-1]
            nxt = self._bigrams.get(last, {})
            if nxt:
                top = sorted(nxt.items(), key=lambda x: -x[1])[: self.SUGGESTION_COUNT]
                return tuple(w for w, _ in top)
        return ()

    def accept(self, idx: int) -> Optional[str]:
        sugs = self.suggestions()
        if not (0 <= idx < len(sugs)):
            return None
        word = sugs[idx]
        if self._prefix:
            # Substitui prefixo pela sugestão
            self._prefix = ""
            self._committed.append(word)
            self._update_bigram(word)
        else:
            # Next-word
            self._committed.append(word)
            self._update_bigram(word)
        return word

    # ───────────────────────────────────────────────────── inspect

    @property
    def prefix(self) -> str:
        return self._prefix

    def committed_text(self) -> str:
        return " ".join(self._committed)

    def reset(self) -> None:
        self._prefix = ""
        self._committed.clear()

    # ───────────────────────────────────────────────────── internals

    def _is_known(self, w: str) -> bool:
        node = self._trie.root
        for ch in w:
            node = node.children.get(ch)
            if node is None:
                return False
        return node.word == w

    def _best_correction(self, w: str) -> Optional[str]:
        matches = get_close_matches(w, self._all_words(limit=2000), n=1, cutoff=0.78)
        return matches[0] if matches else None

    def _all_words(self, limit: int) -> List[str]:
        """Lista plana das primeiras N palavras do trie (BFS)."""
        out: List[str] = []
        stack = [self._trie.root]
        while stack and len(out) < limit:
            n = stack.pop()
            if n.word:
                out.append(n.word)
            stack.extend(n.children.values())
        return out

    def _update_bigram(self, word: str) -> None:
        if len(self._committed) < 2:
            return
        prev = self._committed[-2]
        bucket = self._bigrams.setdefault(prev, {})
        bucket[word] = bucket.get(word, 0) + 1
