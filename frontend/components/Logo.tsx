/**
 * The Excubitor mark: a watchfire burning inside a shield.
 *
 * The whole drawing is one path with two subpaths — the shield, and the flame cut out of it —
 * filled with `evenodd`, so the flame is a real hole rather than a second colour. That is what
 * lets the same mark sit on the dark nav tile, on the brand panel and inside the favicon while
 * taking a single `currentColor`: whatever is behind the shield shows through the fire.
 *
 * The geometry is deliberately coarse. A mark has to survive 16px in a browser tab, so the
 * shield is two straight sides and two curves, and the flame is a body with two tongues and a
 * notch between them — the shape a droplet does not have. A single smooth teardrop renders as
 * water at every size; the notch is what makes a 16px cutout read as fire.
 */

/** Shield: flat top with rounded corners, straight sides, curving to a point at the bottom. */
const SHIELD = "M5.6 4H18.4A1.4 1.4 0 0 1 19.8 5.4V11.9C19.8 16.9 16.4 20.6 12 21.9C7.6 20.6 4.2 16.9 4.2 11.9V5.4A1.4 1.4 0 0 1 5.6 4Z";

/**
 * Flame: a wide body rising into two tongues, the right one taller, sitting slightly high so the
 * shield's point stays clear. The body is deliberately broad and the notch shallow, because both
 * of those are what survive being rasterised into a 16px tab icon.
 */
const FLAME = "M12.8 6C13.6 8 15.4 9.2 15.7 11.5C16.1 13.9 15 16 13.1 16.9C11.4 17.7 9.4 17.2 8.5 15.6C7.6 14.2 7.8 12.7 8.6 11.5C9.3 10.5 10.1 9.9 10.2 8.9C10.2 8.1 9.8 7.4 9.5 6.8C10.4 7.8 11.3 8.8 11.8 10C12 10.5 12.1 11.1 12.1 11.7C12.4 10.6 12.7 9.4 12.7 8.3C12.75 7.5 12.78 6.7 12.8 6Z";

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
