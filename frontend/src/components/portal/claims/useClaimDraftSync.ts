import { useEffect, useRef, useState } from "react";
import {
  useClaimFormDraft,
  useDeleteClaimFormDraft,
  useSaveClaimFormDraft,
  type ClaimFormDraftData,
} from "@/api/portal";

export type ClaimDraftStatus = "idle" | "saving" | "saved" | "error";

interface ClaimDraftSyncOptions {
  data: ClaimFormDraftData;
  ready: boolean;
  meaningful: boolean;
  busy: boolean;
  onRestore: (data: ClaimFormDraftData) => void;
}

export function useClaimDraftSync({
  data,
  ready,
  meaningful,
  busy,
  onRestore,
}: ClaimDraftSyncOptions) {
  const remote = useClaimFormDraft();
  const save = useSaveClaimFormDraft();
  const remove = useDeleteClaimFormDraft();
  const initialized = useRef(false);
  const stopped = useRef(false);
  const restoring = useRef(false);
  const lastSaved = useRef("");
  const failedPayload = useRef("");
  const pendingSave = useRef<Promise<void> | null>(null);
  const [restored, setRestored] = useState(false);
  const [status, setStatus] = useState<ClaimDraftStatus>("idle");
  const serialized = JSON.stringify(data);

  useEffect(() => {
    if (!ready || remote.isLoading || initialized.current) return;
    if (remote.data) {
      restoring.current = true;
      onRestore(remote.data.form_data);
      lastSaved.current = JSON.stringify(remote.data.form_data);
      setRestored(true);
      setStatus("saved");
    } else {
      lastSaved.current = serialized;
    }
    initialized.current = true;
  }, [onRestore, ready, remote.data, remote.isLoading, serialized]);

  useEffect(() => {
    if (restoring.current && serialized === lastSaved.current) {
      restoring.current = false;
    }
    if (
      !initialized.current ||
      restoring.current ||
      stopped.current ||
      !ready ||
      busy ||
      save.isPending ||
      serialized === lastSaved.current ||
      serialized === failedPayload.current ||
      (!remote.data && !meaningful)
    ) {
      return;
    }
    const timer = window.setTimeout(() => {
      setStatus("saving");
      const operation = save
        .mutateAsync({
          form_data: data,
          expected_version: remote.data?.version ?? null,
        })
        .then(() => {
          failedPayload.current = "";
          lastSaved.current = serialized;
          setStatus("saved");
        })
        .catch(() => {
          failedPayload.current = serialized;
          setStatus("error");
          void remote.refetch();
        })
        .finally(() => {
          if (pendingSave.current === operation) pendingSave.current = null;
        });
      pendingSave.current = operation;
    }, 900);
    return () => window.clearTimeout(timer);
  }, [
    busy,
    data,
    meaningful,
    ready,
    remote.data,
    save,
    save.isPending,
    serialized,
  ]);

  useEffect(() => {
    if (remote.isError) setStatus("error");
  }, [remote.isError]);

  const clear = async () => {
    stopped.current = true;
    lastSaved.current = serialized;
    await pendingSave.current;
    await remove.mutateAsync();
  };

  return {
    status,
    restored,
    clear,
    hasUnsavedChanges:
      initialized.current && serialized !== lastSaved.current,
  };
}
