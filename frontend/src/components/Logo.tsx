/** Brand mark: stacked draft cards forming a "3D stack" glyph -- transparent background so it
 * reads on both the light and dark app surfaces (docs/assets/redraft-mark.svg mirrors this
 * exactly, for the README). Square viewBox; `size` sets both width and height. */
export function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <rect x="22" y="24" width="34" height="22" rx="4" fill="none" stroke="#3987e5" strokeWidth="2.5" />
      <rect x="15" y="17" width="34" height="22" rx="4" fill="none" stroke="#2a78d6" strokeWidth="2.5" />
      <rect x="8" y="10" width="34" height="22" rx="4" fill="#2a78d6" />
      <line x1="17" y1="21" x2="29" y2="21" stroke="#ffffff" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="17" cy="21" r="3" fill="#ffffff" />
      <circle cx="30" cy="21" r="2.3" fill="#ffffff" />
    </svg>
  );
}
