"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Save, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { useProfiles } from "@/hooks/use-profiles";
import { useCreateManualModel, type DraftModel, type BuilderTarget } from "@/hooks/use-builder";
import { draftToModelJson } from "./assemble";
import { modelDetailPath } from "@/lib/model-routes";
import { formatApiError } from "@/lib/api/errors";

export interface SaveControlProps {
  draft: DraftModel;
  /** The sample the model was authored against (used to derive routing keys). */
  sampleId: string;
  /** Owning profile, chosen in setup or pre-bound from a profile page. */
  profileId: string | null;
  /** What the model was authored against, so the saved rules carry the same
   * profile binding the preview used. */
  target: BuilderTarget;
}

/** Persist the draft as a candidate model. When a profile is already chosen the
 * target is fixed; a model authored without one still needs a profile to live
 * under, so it is picked here instead. */
export function SaveControl({ draft, sampleId, profileId, target }: SaveControlProps) {
  const router = useRouter();
  const { data: profiles } = useProfiles();
  const create = useCreateManualModel();
  const [picked, setPicked] = useState<string | null>(null);

  const saveTarget = profileId ?? target.profileId ?? picked;
  const boundProfile = (profiles ?? []).find((p) => p.profile_id === saveTarget);

  const save = () => {
    if (!saveTarget) return;
    create.mutate(
      {
        profileId: saveTarget,
        sampleIntakeId: sampleId,
        modelJson: draftToModelJson(draft, saveTarget),
      },
      {
        onSuccess: (model) => {
          toast.success("Candidate model created");
          router.push(modelDetailPath(model.id));
        },
        onError: (e) => toast.error(formatApiError(e)),
      },
    );
  };

  return (
    <div className="flex items-center gap-2">
      {profileId || target.profileId ? (
        boundProfile && (
          <span className="text-sm text-muted-foreground">
            for <span className="font-medium text-foreground">{boundProfile.identity.canonical_name}</span>
          </span>
        )
      ) : (
        <Select value={picked ?? ""} onValueChange={setPicked}>
          <SelectTrigger className="h-9 w-52">
            {picked ? (
              (profiles ?? []).find((p) => p.profile_id === picked)?.identity.canonical_name ?? picked
            ) : (
              <span className="text-muted-foreground">Save under profile…</span>
            )}
          </SelectTrigger>
          <SelectContent>
            {(profiles ?? []).map((p) => (
              <SelectItem key={p.profile_id} value={p.profile_id}>
                {p.identity.canonical_name} ({p.profile_id})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <Button size="sm" onClick={save} disabled={!saveTarget || create.isPending}>
        {create.isPending ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        ) : (
          <Save className="mr-2 h-4 w-4" />
        )}
        Save as candidate
      </Button>
    </div>
  );
}
