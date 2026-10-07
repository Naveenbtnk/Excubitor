"use client";

import { useCallback } from "react";
import useSWRInfinite from "swr/infinite";
import { fetcher } from "@/lib/api";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export const PAGE_SIZE = 25;

export function usePaged<T>(path: string, pageSize: number = PAGE_SIZE) {
  const { data, error, isLoading, isValidating, size, setSize, mutate } = useSWRInfinite<Page<T>>(
    (index, previous: Page<T> | null) => {
      if (previous && !previous.has_more) return null;
      return `${path}?limit=${pageSize}&offset=${index * pageSize}`;
    },
    fetcher
  );

  const pages = data ?? [];
  const last = pages[pages.length - 1];

  return {
    items: pages.flatMap((page) => page.items),
    total: pages[0]?.total ?? 0,
    hasMore: Boolean(last?.has_more),
    isLoading,
    loadingMore: isValidating && pages.length < size,
    loadMore: useCallback(() => setSize((value) => value + 1), [setSize]),
    mutate,
    error,
  };
}
