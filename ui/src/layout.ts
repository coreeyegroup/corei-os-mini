export const views = [
  "Overview",
  "Market data",
  "Providers",
  "Accounts",
  "Instruments",
  "Strategies",
  "Execution",
  "Portfolio",
  "Operations",
  "Settings",
] as const;
export type View = (typeof views)[number];
export type Layout = {
  version: 1;
  workspace: string;
  activity: View;
  groups: View[][];
  active: View[];
  focus: number;
  left: boolean;
  right: boolean;
  bottom: boolean;
  axis: "row" | "column";
  leftWidth: number;
  rightWidth: number;
  bottomHeight: number;
  maximized: boolean;
};
export const key = "corei-os-mini.workstation.layout.v1";
export function defaults(workspace = "Research"): Layout {
  return {
    version: 1,
    workspace,
    activity: "Market data",
    groups: [["Overview", "Market data"]],
    active: ["Overview"],
    focus: 0,
    left: true,
    right: false,
    bottom: false,
    axis: "row",
    leftWidth: 220,
    rightWidth: 270,
    bottomHeight: 180,
    maximized: false,
  };
}
export function restore(raw: string | null): Layout {
  try {
    const x = JSON.parse(raw || "null");
    if (
      !x ||
      x.version !== 1 ||
      !["Research", "Execution", "Operations"].includes(x.workspace) ||
      !views.includes(x.activity) ||
      !Array.isArray(x.groups) ||
      x.groups.length < 1 ||
      x.groups.length > 4 ||
      !Array.isArray(x.active) ||
      x.active.length !== x.groups.length
    )
      return defaults();
    if (
      x.groups.some(
        (g: unknown) =>
          !Array.isArray(g) ||
          g.length === 0 ||
          g.length > views.length ||
          new Set(g).size !== g.length ||
          g.some((v: View) => !views.includes(v)),
      )
    )
      return defaults();
    if (x.active.some((v: View, i: number) => !x.groups[i].includes(v)))
      return defaults();
    if (
      !Number.isInteger(x.focus) ||
      x.focus < 0 ||
      x.focus >= x.groups.length ||
      !["row", "column"].includes(x.axis) ||
      ["left", "right", "bottom", "maximized"].some(
        (k) => typeof x[k] !== "boolean",
      )
    )
      return defaults();
    for (const [name, min, max] of [
      ["leftWidth", 160, 380],
      ["rightWidth", 200, 440],
      ["bottomHeight", 120, 400],
    ] as const)
      if (!Number.isFinite(x[name]) || x[name] < min || x[name] > max)
        return defaults();
    return x as Layout;
  } catch {
    return defaults();
  }
}
