import { ShieldCheck, Siren, Sparkles } from "lucide-react";
import { Logo } from "@/components/Logo";

/**
 * The left half of the sign-in page: the brand at panel scale.
 *
 * Two things here are deliberately not JavaScript. The pulse behind the mark is a pair of CSS
 * pseudo-elements expanding on a loop (`watch-rings`, defined in globals.css) and the glow is two
 * blurred radial washes, so the front door has something alive in it without a single byte of
 * animation code -- and it stands still for anyone whose system asks for reduced motion.
 *
 * The panel is `hidden` below `lg`, where the sign-in column carries a compact mark and wordmark
 * instead: on a phone the brand should be a header, not half a screen before the form.
 */
export function BrandPanel() {
  return (
    <section
      aria-labelledby="brand-panel-name"
      className="relative hidden overflow-hidden bg-neutral-900 px-10 py-12 text-brand-cream lg:flex lg:flex-col lg:justify-between xl:px-14 xl:py-16"
    >
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="absolute -left-24 top-1/4 size-[26rem] rounded-full bg-brand-rose/25 blur-3xl" />
        <div className="absolute -right-16 -top-24 size-96 rounded-full bg-amber-100/10 blur-3xl" />
      </div>

      <div className="relative flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.22em] text-brand-cream/60">
        <Logo size={16} />
        CloudOps AI
      </div>

      <div className="relative">
        <div className="relative size-20">
          {/* Cream on near-black: the mark is one path and takes the panel's own colour, so it
              reads at 80px here and at 20px in the nav without a second drawing. */}
          <span aria-hidden className="watch-rings absolute inset-0" />
          <Logo size={80} className="relative text-brand-cream" />
        </div>

        <h1 id="brand-panel-name" className="mt-10 text-4xl font-semibold tracking-tight">
          Excubitor
        </h1>
        <p className="mt-3 text-lg text-brand-cream/75">Keeps watch so you don&rsquo;t have to.</p>

        <ul className="mt-10 space-y-3 text-sm text-brand-cream/65">
          <li className="flex items-center gap-2.5">
            <Sparkles size={15} aria-hidden className="shrink-0 text-brand-rose" />
            Gemini triage from the alert to the root cause
          </li>
          <li className="flex items-center gap-2.5">
            <Siren size={15} aria-hidden className="shrink-0 text-brand-rose" />
            Every status change and comment on one incident timeline
          </li>
          <li className="flex items-center gap-2.5">
            <ShieldCheck size={15} aria-hidden className="shrink-0 text-brand-rose" />
            RAG post-mortems with owners and due dates
          </li>
        </ul>
      </div>

      {/* Cream at 70% over the panel's near-black lands around 8:1, which clears AA for text
          this size; the 45% this used to carry measured about 4:1 and read as a smudge. */}
      <p className="relative text-xs text-brand-cream/70">
        Alert triage, incident timelines and post-mortems for teams on call.
      </p>
    </section>
  );
}
