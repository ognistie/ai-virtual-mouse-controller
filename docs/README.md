# 🛹 Site oficial — AI Virtual Mouse Controller

Site estático em HTML/CSS/JS puro com estética **cyber-grunge + Y2K dark rave + skate zine 1999 + brutalismo web**. Pronto pra rodar no GitHub Pages.

> Esta pasta vai **dentro** do repositório do projeto `ai-virtual-mouse-controller`, como `docs/`. NÃO é o site do AI Farm Agent (esse é um projeto separado).

## 🎨 Identidade visual

Mistura intencional de 4 referências:

| Referência | Como aparece |
|---|---|
| **Thrasher Magazine 1999** | Papel manilha sujo, carimbos amarelo/vermelho, layout de tabela HTML antiga |
| **Crystal Castles / Electroclash** | Glitch RGB nos hovers, animação de tremor no título, scanlines globais |
| **exp.typo / orthias** | Tipografia distorcida sobre papel, rotações sutis em cards, ruído granulado |
| **Brutalismo web** | Bordas grossas pretas, sombras chapadas offset, sem gradientes suaves |

### Paleta

| Cor | Hex | Onde usar |
|---|---|---|
| 📜 Papel manilha | `#e8e2d4` | Background principal |
| ⚫ Tinta preta | `#0a0a0a` | Textos, headers, bordas |
| 🔴 Vermelho sujo | `#c1281a` | Accent principal (Thrasher) |
| 🟡 Amarelo ácido | `#ffdd00` | Carimbos, highlights, hover |
| 🟢 Verde rave | `#aeff00` | Status "OK", success |
| 💗 Magenta rave | `#ff0080` | Glitch RGB |
| 💙 Ciano cyber | `#00d4ff` | Glitch RGB |

### Fontes (Google Fonts grátis)

| Família | Uso |
|---|---|
| **Bowlby One** | Display chunky (títulos H1, H2, botões) |
| **Space Mono** | Body monospace (texto corrido, code) |
| **Special Elite** | Stencil (labels, navegação, carimbos) |

## 📁 Estrutura final no repo

```
ai-virtual-mouse-controller/         ← repo no GitHub (você cria)
├── main.py
├── config.py
├── requirements.txt
├── README.md                         ← README técnico do código Python
├── core/
│   ├── camera.py
│   ├── cursor_controller.py
│   ├── gesture_detector.py
│   ├── hand_tracker.py
│   ├── runtime_settings.py
│   ├── smoothing.py
│   ├── ui_overlay.py
│   └── utils.py
├── services/
│   └── virtual_mouse_service.py
├── tests/
│   └── ...
└── docs/                             ← ESTA PASTA (o site)
    ├── index.html                    # Home + install + feedback
    ├── usage.html                    # Guia de uso completo
    ├── about.html                    # História + sobre o dev
    ├── README.md                     # Este arquivo
    └── assets/
        ├── css/style.css
        ├── js/main.js
        └── img/                      # (vazia, pra screenshots futuras)
```

## 🚀 Deploy no GitHub Pages

### Passo 1 — Cria o repo público

```powershell
# No GitHub: cria novo repositório público "ai-virtual-mouse-controller"
# Marca como público
```

### Passo 2 — Inicializa git localmente e sobe tudo

```powershell
cd c:\Users\Paulo\Downloads\ai-virtual-mouse-controller
git init
git remote add origin https://github.com/ognistie/ai-virtual-mouse-controller.git

# Verifica que tem a pasta docs/
dir

git add .
git commit -m "Initial public release v6.9.1 + project website"
git branch -M main
git push -u origin main
```

### Passo 3 — Ativa GitHub Pages

1. No repo do GitHub, vai em **Settings** → **Pages**
2. **Source**: `Deploy from a branch`
3. **Branch**: `main` / **Folder**: `/docs`
4. Clica **Save**

Em 1-2 minutos o site aparece em:

```
https://ognistie.github.io/ai-virtual-mouse-controller/
```

## ✨ Features do site

- **Bilíngue PT/EN** — toggle no canto superior direito, persistido em localStorage
- **Copy buttons automáticos** em todos os snippets de código
- **Konami code easter egg** — `↑↑↓↓←→←→ B A` inverte o site
- **Responsivo total** — desktop + tablet + mobile
- **Zero build step** — HTML/CSS/JS vanilla, push e roda
- **~80KB total** descompactado (~20KB gzipped)
- **Animações controladas** — tremor no hover do título, glitch RGB no brand, blink no cursor de terminal

## 🔧 Como editar

### Mudar texto bilíngue

Cada elemento tem dois atributos:

```html
<h2 data-pt="TÍTULO EM PT" data-en="TITLE IN EN">TÍTULO EM PT</h2>
```

O JS troca conforme o idioma escolhido.

### Mudar cores

Topo do `assets/css/style.css`, variáveis `:root`:

```css
:root {
    --paper: #e8e2d4;       /* fundo principal */
    --red: #c1281a;         /* vermelho Thrasher */
    --yellow: #ffdd00;      /* amarelo carimbo */
    /* ... */
}
```

### Adicionar foto sua

Substitui o placeholder `G` em `about.html`:

```html
<div class="profile-pic">
    <img src="assets/img/me.jpg" alt="Guilherme"
         style="width:100%;height:100%;object-fit:cover;">
</div>
```

E coloca a foto em `docs/assets/img/me.jpg`.

## 🐛 Conhecidos

- ASCII art do footer pode quebrar em telas < 320px (mantém legível)
- Konami code requer teclado físico (não funciona em mobile)
- Sem screenshots reais do projeto ainda — adicionar um GIF/vídeo daria muito mais impacto

## 📝 Licença

Mesma do projeto principal.