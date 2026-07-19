/** Typed calls + query hooks for the insured-entity alias map.
 *
 * An alias bridges two spellings of one legal entity so the matching gate
 * treats them as equal. Neither side is rewritten: the category keeps the
 * registered name (the placement-slip export reproduces it verbatim) and the
 * roster keeps its own.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useSession } from "@/stores/session";

export interface EntityAlias {
  id: string;
  alias: string;
  canonical: string;
  /** normalize_entity(alias) — shown so it's clear why two spellings already match. */
  alias_normalized: string;
}

export interface EntityAliasInput {
  alias: string;
  canonical: string;
}

function useAliasInvalidator() {
  const qc = useQueryClient();
  // Aliases change which employees match, so the vocabulary (and its
  // reconciliation flags) and any match results go stale with them.
  return () => {
    void qc.invalidateQueries({ queryKey: ["entity-aliases"] });
    void qc.invalidateQueries({ queryKey: ["entity-vocab"] });
    void qc.invalidateQueries({ queryKey: ["match-results"] });
  };
}

export function useEntityAliases() {
  const cid = useSession((s) => s.activeClientId);
  return useQuery({
    queryKey: ["entity-aliases", cid],
    queryFn: () => api.get<EntityAlias[]>("/entity-aliases"),
  });
}

export function useCreateEntityAlias() {
  const invalidate = useAliasInvalidator();
  return useMutation({
    mutationFn: (input: EntityAliasInput) =>
      api.post<EntityAlias>("/entity-aliases", input),
    onSuccess: invalidate,
  });
}

export function useUpdateEntityAlias() {
  const invalidate = useAliasInvalidator();
  return useMutation({
    mutationFn: (input: { id: string } & Partial<EntityAliasInput>) => {
      const { id, ...body } = input;
      return api.patch<EntityAlias>(`/entity-aliases/${id}`, body);
    },
    onSuccess: invalidate,
  });
}

export function useDeleteEntityAlias() {
  const invalidate = useAliasInvalidator();
  return useMutation({
    mutationFn: (id: string) => api.delete<void>(`/entity-aliases/${id}`),
    onSuccess: invalidate,
  });
}
