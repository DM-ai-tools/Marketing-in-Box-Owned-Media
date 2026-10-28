import { useEffect, useState } from "react";
import { animate } from "framer-motion";

/** A number that counts up from 0 to `to`, once. Static under reduced motion (`still`). Used by the
 * keyword cards' measured volumes. */
export function CountUp({ to, still, duration = 0.9 }: { to: number; still?: boolean; duration?: number }) {
  const [value, setValue] = useState(still ? to : 0);
  useEffect(() => {
    if (still) {
      setValue(to);
      return;
    }
    const controls = animate(0, to, { duration, ease: "easeOut", onUpdate: (v) => setValue(Math.round(v)) });
    return () => controls.stop();
  }, [to, still, duration]);
  return <span className="tabular-nums">{value.toLocaleString("en-US")}</span>;
}
