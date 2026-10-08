import { ConfirmDialog } from "../components/ConfirmDialog";
import { useUiStore } from "../store/uiStore";
import { usePipelineStore } from "./pipelineStore";

/** The notice that the Plan of Action is out of date.
 *
 * The plan is one document for the whole engagement, written after Phase 2, and it reads every
 * sub-service built at the time. A sub-service built or changed afterwards is not in it — and the
 * plan does not regenerate on its own, because a plan is a stage the operator approves. So the end
 * of that sub-service's run says so and offers the re-run, rather than leaving a plan that quietly
 * stops describing the work.
 *
 * Mounted at the app root for the reason `ChatDeleteDialog` is: it is raised from the pipeline
 * store, and about the whole app rather than the pane on screen. "Later" is the safe default and
 * takes focus — a plan costs a long generation, and the operator may want to finish the other
 * sub-services first and re-run it once.
 */
export function PlanRerunDialog() {
  const target = useUiStore((s) => s.pendingPlanRerun);
  const dismiss = useUiStore((s) => s.dismissPlanRerun);
  const startAtStage = usePipelineStore((s) => s.startAtStage);

  return (
    <ConfirmDialog
      open={target !== null}
      title="Re-run the Plan of Action"
      message={`${target?.subService ? `${target.subService} was` : "A sub-service was"} built after your Plan of Action was written, so the plan doesn't cover it yet. Re-run the Plan of Action to include the latest changes.`}
      detail="One plan covers every sub-service. Re-running it reads each sub-service as it stands now and keeps the earlier plan as a previous version."
      confirmLabel="Re-run Plan of Action"
      cancelLabel="Later"
      footnote="You can start it any time from the Plan of Action stage."
      onCancel={dismiss}
      onConfirm={() => {
        dismiss();
        startAtStage("plan_of_action");
      }}
    />
  );
}
