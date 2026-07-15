import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { ProductRegistry } from "@/types";

/**
 * The backend's static product-classification catalog (known product codes →
 * line / form profile / layout family, plus the selectable profiles and
 * lines). Replaces the old hardcoded client-side code→line map. Static data —
 * cached for the whole session.
 */
export function useRegistry() {
  return useQuery({
    queryKey: ["product-registry"],
    queryFn: () => api.get<ProductRegistry>("/product-registry"),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}
