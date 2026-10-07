import { clsx } from "clsx";

interface Props {
  showing: number;
  total: number;
  hasMore: boolean;
  loading?: boolean;
  onLoadMore: () => void;
  className?: string;
}

export function LoadMore({
  showing,
  total,
  hasMore,
  loading = false,
  onLoadMore,
  className,
}: Props) {
  if (total === 0) return null;

  return (
    <div
      className={clsx(
        "flex flex-wrap items-center justify-between gap-2 text-xs text-neutral-500",
        className
      )}
    >
      <span>
        Showing {showing} of {total}
      </span>
      {hasMore && (
        <button onClick={onLoadMore} disabled={loading} className="btn-secondary px-3 py-1.5">
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
