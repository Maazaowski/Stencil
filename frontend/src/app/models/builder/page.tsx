"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { BuilderWorkspace } from "@/components/builder/builder-workspace";
import { BuilderSetup } from "@/components/builder/builder-setup";
import { useProfile } from "@/hooks/use-profiles";
import type { BuilderTarget } from "@/hooks/use-builder";

function BuilderGate() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const profileId = searchParams.get("profileId");
  const { data: profile } = useProfile(profileId ?? "");

  const [started, setStarted] = useState<{
    sample: { id: string; name: string };
    target: BuilderTarget;
  } | null>(null);

  const boundName = profile?.identity.canonical_name;
  const description = started
    ? `Authoring against ${started.sample.name}${boundName ? ` · for ${boundName}` : ""}`
    : "Choose what the model delivers, then add a sample invoice to author against.";

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <PageHeader title="Model Builder" description={description} />
        <Button variant="outline" size="sm" onClick={() => router.push("/models")}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to models
        </Button>
      </div>

      {!started ? (
        <BuilderSetup
          profileId={profileId}
          onReady={(sample, target) => setStarted({ sample, target })}
        />
      ) : (
        <BuilderWorkspace
          sampleId={started.sample.id}
          sampleName={started.sample.name}
          target={started.target}
        />
      )}
    </div>
  );
}

export default function ModelBuilderPage() {
  return (
    <Suspense fallback={null}>
      <BuilderGate />
    </Suspense>
  );
}
