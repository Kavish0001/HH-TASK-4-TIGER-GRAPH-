"use client";

// Every pane on the case view reads from this hook, never from the stream directly, so
// SSE, polling and the mock replay all look the same to the components.

import { useCallback, useEffect, useRef, useState } from "react";
import { approveAction, getCase, runCase } from "./api";
import type { Action, InternalCase, RunState } from "./types";

export function useCaseRun(id: string, autoRun: boolean) {
  const [data, setData] = useState<InternalCase | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [run, setRun] = useState<RunState>("idle");
  const [runDetail, setRunDetail] = useState<string>("");
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const cancel = useRef<(() => void) | null>(null);
  const autoRan = useRef(false);

  const start = useCallback(
    (from?: InternalCase) => {
      const base = from ?? data;
      if (!base) return;
      cancel.current?.();
      setStartedAt(Date.now());
      setRunDetail("");
      cancel.current = runCase(id, base, {
        onCase: setData,
        onState: (s, detail) => {
          setRun(s);
          if (detail) setRunDetail(detail);
        },
      });
    },
    [id, data],
  );

  useEffect(() => {
    let alive = true;
    setData(null);
    setLoadError(null);
    getCase(id)
      .then((c) => {
        if (!alive) return;
        setData(c);
        if (autoRun && !autoRan.current) {
          autoRan.current = true;
          start(c);
        }
      })
      .catch((e: Error) => alive && setLoadError(e.message));
    return () => {
      alive = false;
    };
    // start is intentionally excluded: I only want the auto run on first load of an id.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, autoRun]);

  useEffect(() => () => cancel.current?.(), []);

  const decide = useCallback(
    async (action: Action, approved: boolean, note: string) => {
      const c = await approveAction(id, {
        action,
        approved,
        decision: approved ? "approve" : "reject",
        analyst: "analyst",
        note,
      });
      setData(c);
      return c;
    },
    [id],
  );

  return { data, loadError, run, runDetail, startedAt, start: () => start(), decide };
}
