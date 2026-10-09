/**
 * The Excubitor mark: a watchfire burning inside a shield.
 *
 * The whole drawing is one path with two subpaths — the shield, and the flame cut out of it —
 * filled with `evenodd`, so the flame is a real hole rather than a second colour. That is what
 * lets the same mark sit on the dark nav tile, on the brand panel and inside the favicon while
 * taking a single `currentColor`: whatever is behind the shield shows through the fire.
 *
 * The geometry is deliberately coarse. A mark has to survive 16px in a browser tab, so the
 * shield is two straight sides and two curves, and the flame is a teardrop with one lean to the
 * right — no detail that a small render would turn into noise.
 */

/** Shield: flat top with rounded corners, straight sides, curving to a point at the bottom. */
const SHIELD = "M5.6 4H18.4A1.4 1.4 0 0 1 19.8 5.4V11.9C19.8 16.9 16.4 20.6 12 21.9C7.6 20.6 4.2 16.9 4.2 11.9V5.4A1.4 1.4 0 0 1 5.6 4Z";

/** Flame: teardrop leaning right, sitting slightly high so the shield's point stays clear. */
const FLAME = "M12.5 7C13.5 9.3 14.5 10.2 15.1 11.5C15.8 13 15.9 13.8 15.5 14.8C14.9 16.3 13.5 17.2 12 17.2C10.3 17.2 8.9 16 8.9 14.2C8.9 12.6 9.7 11.5 10.5 10.3C11.3 9 12 8.1 12.5 7Z";

/** The mark's path data, shared with the favicon so the two cannot drift apart. */
export const LOGO_PATH = `${SHIELD} ${FLAME}`;

export interface LogoProps {
  size?: number;
  className?: string;
  /** Give the mark an accessible name. Omit it next to a wordmark, where it is decoration. */
  title?: string;
}

export function Logo({ size = 24, className, title }: LogoProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className={className}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      fillRule="evenodd"
      clipRule="evenodd"
    >
      {title ? <title>{title}</title> : null}
      <path d={LOGO_PATH} fill="currentColor" />
    </svg>
  );
}
