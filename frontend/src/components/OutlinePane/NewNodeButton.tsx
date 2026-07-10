import { useState } from "react";
import { CreateNodeDialog } from "../dialogs/CreateNodeDialog";

export function NewNodeButton({ defaultParentId }: { defaultParentId?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="btn btn--primary btn--sm" onClick={() => setOpen(true)}>
        + New node
      </button>
      {open ? <CreateNodeDialog defaultParentId={defaultParentId} onClose={() => setOpen(false)} /> : null}
    </>
  );
}
