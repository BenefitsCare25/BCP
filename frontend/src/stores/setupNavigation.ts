import { create } from "zustand";

type ContextAction = () => void;
type ContextGuard = (action: ContextAction) => void;

/** Context selectors change stores without a router navigation. */
export const useSetupNavigation = create<{
  guard: ContextGuard | null;
  setGuard: (guard: ContextGuard | null) => void;
}>((set) => ({ guard: null, setGuard: (guard) => set({ guard }) }));

export function requestSetupContextChange(action: ContextAction) {
  const guard = useSetupNavigation.getState().guard;
  if (guard) guard(action);
  else action();
}
