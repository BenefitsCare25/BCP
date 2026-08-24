import {
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import {
  useClaimFormDraft,
  useDeleteClaimFormDraft,
  useSaveClaimFormDraft,
  type ClaimFormDraft,
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

interface DraftSaveContext {
  data: ClaimFormDraftData;
  serialized: string;
  version: MutableRefObject<number | null>;
  lastSaved: MutableRefObject<string>;
  failedPayload: MutableRefObject<string>;
  pendingSave: MutableRefObject<Promise<void> | null>;
  setStatus: Dispatch<SetStateAction<ClaimDraftStatus>>;
  mutate: (input: {
    form_data: ClaimFormDraftData;
    expected_version: number | null;
  }) => Promise<ClaimFormDraft>;
  onError: () => void;
}

function startDraftSave(context: DraftSaveContext) {
  context.setStatus("saving");
  const operation = context
    .mutate({
      form_data: context.data,
      expected_version: context.version.current,
    })
    .then((draft) => {
      context.version.current = draft.version;
      context.failedPayload.current = "";
      context.lastSaved.current = context.serialized;
      context.setStatus("saved");
    })
    .catch((error: unknown) => {
      context.failedPayload.current = context.serialized;
      context.setStatus("error");
      context.onError();
      throw error;
    })
    .finally(() => {
      if (context.pendingSave.current === operation) {
        context.pendingSave.current = null;
      }
    });
  context.pendingSave.current = operation;
  return operation;
}

interface DraftRestoreOptions {
  ready: boolean;
  loading: boolean;
  draft: ClaimFormDraft | null | undefined;
  serialized: string;
  initialized: MutableRefObject<boolean>;
  restoring: MutableRefObject<boolean>;
  lastSaved: MutableRefObject<string>;
  version: MutableRefObject<number | null>;
  onRestore: (data: ClaimFormDraftData) => void;
  setStatus: Dispatch<SetStateAction<ClaimDraftStatus>>;
}

function useRestoreClaimDraft(options: DraftRestoreOptions) {
  useEffect(() => {
    if (!options.ready || options.loading || options.initialized.current) return;
    if (options.draft) {
      options.restoring.current = true;
      options.onRestore(options.draft.form_data);
      options.lastSaved.current = JSON.stringify(options.draft.form_data);
      options.version.current = options.draft.version;
      options.setStatus("saved");
    } else {
      options.lastSaved.current = options.serialized;
      options.version.current = null;
    }
    options.initialized.current = true;
  }, [options]);

  useEffect(() => {
    if (options.draft) options.version.current = options.draft.version;
  }, [options.draft, options.version]);
}

export function useClaimDraftSync(options: ClaimDraftSyncOptions) {
  const { data, ready, meaningful, busy, onRestore } = options;
  const remote = useClaimFormDraft();
  const save = useSaveClaimFormDraft();
  const remove = useDeleteClaimFormDraft();
  const initialized = useRef(false);
  const stopped = useRef(false);
  const restoring = useRef(false);
  const lastSaved = useRef("");
  const failedPayload = useRef("");
  const version = useRef<number | null>(null);
  const pendingSave = useRef<Promise<void> | null>(null);
  const saveTimer = useRef<number | null>(null);
  const [status, setStatus] = useState<ClaimDraftStatus>("idle");
  const serialized = JSON.stringify(data);

  useRestoreClaimDraft({
    ready,
    loading: remote.isLoading,
    draft: remote.data,
    serialized,
    initialized,
    restoring,
    lastSaved,
    version,
    onRestore,
    setStatus,
  });

  useEffect(() => {
    if (restoring.current && serialized === lastSaved.current) {
      restoring.current = false;
    }
    if (
      !initialized.current || restoring.current || stopped.current || !ready ||
      busy || save.isPending || serialized === lastSaved.current ||
      serialized === failedPayload.current || (!remote.data && !meaningful)
    ) return;
    const timer = window.setTimeout(() => {
      saveTimer.current = null;
      void startDraftSave({
        data, serialized, version, lastSaved, failedPayload, pendingSave,
        setStatus, mutate: save.mutateAsync,
        onError: () => void remote.refetch(),
      }).catch(() => undefined);
    }, 900);
    saveTimer.current = timer;
    return () => {
      window.clearTimeout(timer);
      if (saveTimer.current === timer) saveTimer.current = null;
    };
  }, [busy, data, meaningful, ready, remote, save, serialized]);

  useEffect(() => {
    if (remote.isError) setStatus("error");
  }, [remote.isError]);

  const saveNow = async () => {
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = null;
    try {
      await pendingSave.current;
    } catch {
      const refreshed = await remote.refetch();
      version.current = refreshed.data?.version ?? null;
    }
    if (serialized === lastSaved.current && version.current !== null) {
      setStatus("saved");
      return;
    }
    await startDraftSave({
      data, serialized, version, lastSaved, failedPayload, pendingSave,
      setStatus, mutate: save.mutateAsync,
      onError: () => void remote.refetch(),
    });
  };

  const clear = async () => {
    stopped.current = true;
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = null;
    await pendingSave.current?.catch(() => undefined);
    lastSaved.current = serialized;
    try {
      await remove.mutateAsync();
      version.current = null;
      setStatus("idle");
    } catch (error) {
      stopped.current = false;
      throw error;
    }
  };

  return {
    status,
    saveNow,
    clear,
  };
}
