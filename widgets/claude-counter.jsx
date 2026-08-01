// Ubersicht widget: the counter card on the Mac desktop. Draggable.
//
// Install: cp widgets/claude-counter.jsx to the Ubersicht widgets folder.
//          render.py copies claude-counter.png in beside it on every run.
//
// TWO THINGS THAT ARE NOT OBVIOUS:
//
// 1. The image MUST be referenced relatively, never as file://. Ubersicht renders
//    widgets in a WebKit view served from http://localhost:41416, so file:// is
//    cross-origin and gets blocked silently. The desktop then shows only alt text
//    and a rounded border, which reads as a broken layout, not a blocked request.
//
// 2. Default position is TOP-LEFT, not top-right. macOS fills desktop icons from
//    the RIGHT edge, wrapping into more columns as you accumulate files, so a card
//    pinned to the right will eventually sit on top of them. Left is safe. Drag it
//    wherever you like - the position is remembered.
//
// Dragging requires Ubersicht interaction mode:
//   defaults write tracesOf.Uebersicht enableInteraction -bool true
// The position is saved to localStorage, so it survives refreshes and restarts.

const CARD = "claude-counter.png";
const KEY = "claudeCounterPos";
const DX = 48;
const DY = 60;

function loadPos() {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return { x: DX, y: DY };
    const p = JSON.parse(raw);
    // Clamp back on-screen. A position saved on a wider display would otherwise
    // strand the card off the edge with no way to grab it back.
    const mx = Math.max(window.innerWidth - 220, 0);
    const my = Math.max(window.innerHeight - 140, 0);
    return { x: Math.min(Math.max(p.x, 0), mx), y: Math.min(Math.max(p.y, 0), my) };
  } catch (e) {
    return { x: DX, y: DY };
  }
}

function startDrag(e) {
  e.preventDefault();
  const el = e.currentTarget;
  const from = { mx: e.clientX, my: e.clientY,
                 x: parseInt(el.style.left, 10) || 0, y: parseInt(el.style.top, 10) || 0 };
  el.style.cursor = "grabbing";
  const move = (ev) => {
    el.style.left = from.x + (ev.clientX - from.mx) + "px";
    el.style.top  = from.y + (ev.clientY - from.my) + "px";
  };
  const up = () => {
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
    el.style.cursor = "grab";
    try {
      window.localStorage.setItem(KEY, JSON.stringify({
        x: parseInt(el.style.left, 10) || 0, y: parseInt(el.style.top, 10) || 0 }));
    } catch (err) { /* private mode: position just will not persist */ }
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
}

// This command does two jobs: it reports the card's mtime (used as a cache-buster
// and staleness clock), and it mirrors the phone card into iCloud.
//
// The mirror lives HERE rather than in the hourly launchd job for a measured
// reason: a LaunchAgent can create new files in iCloud Drive but cannot overwrite
// one iCloud has marked UF_TRACKED, which it does to everything it syncs. Every
// strategy fails - copy over, truncate, rename-over, even unlink-then-write. An
// Ubersicht child process is not subject to that guard and copies fine, verified
// with a probe widget. So this removes the Full Disk Access requirement entirely.
export const command = `
  W="$HOME/Library/Application Support/Übersicht/widgets/${CARD}"
  OUT="$HOME/claude-code/gh/counter/out"
  for D in "$HOME/Library/Mobile Documents/com~apple~CloudDocs/ClaudeCounter" \
           "$HOME/Library/Mobile Documents/iCloud~dk~simonbs~Scriptable/Documents/ClaudeCounter"; do
    [ -d "$(dirname "$D")" ] || continue
    mkdir -p "$D" 2>/dev/null
    for F in card.png stats.json; do
      if [ ! -f "$D/$F" ] || [ "$OUT/$F" -nt "$D/$F" ]; then
        cp "$OUT/$F" "$D/$F" 2>/dev/null
      fi
    done
    # Flat copy one level up as well. A subfolder has to propagate as a directory
    # before iOS can see anything inside it; a file in Documents root does not.
    P="$(dirname "$D")/ClaudeCounter-stats.json"
    if [ ! -f "$P" ] || [ "$OUT/stats.json" -nt "$P" ]; then
      cp "$OUT/stats.json" "$P" 2>/dev/null
    fi
  done
  stat -f %m "$W" 2>/dev/null || echo 0
`;

// Poll for a fresh render every 2 minutes; the render itself runs every 10.
export const refreshFrequency = 120000;

export const className = `
  top: 0; left: 0; z-index: 0;
  .card { position: absolute; width: 640px; cursor: grab; }
  .card img { width: 100%; display: block; border-radius: 22px;
              box-shadow: 0 26px 60px -18px rgba(0,0,0,.75);
              -webkit-user-drag: none; }
  .stale { font: 400 11px -apple-system, sans-serif; color: rgba(255,255,255,.42);
           text-align: right; margin-top: 8px; }
`;

export const render = ({ output }) => {
  const mtime = parseInt(String(output).trim(), 10) || 0;
  const pos = loadPos();
  const ageHours = mtime ? (Date.now() / 1000 - mtime) / 3600 : 0;
  return (
    <div className="card" style={{ left: pos.x, top: pos.y }} onMouseDown={startDrag}>
      <img src={`${CARD}?v=${mtime}`} alt="" />
      {ageHours > 3 && (
        <div className="stale">card is {Math.floor(ageHours)}h old, check launchd</div>
      )}
    </div>
  );
};
