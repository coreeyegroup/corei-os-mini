const paths=[
 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
 'M3 17l5-7 4 4 5-10 4 3 M3 21h18',
 'M3 7h14l-3-3 M21 17H7l3 3 M17 7l-3 3 M7 17l3-3',
 'M4 21v-3a5 5 0 015-5h6a5 5 0 015 5v3 M16 6a4 4 0 11-8 0 4 4 0 018 0',
 'M4 4h16v16H4z M8 8h8 M8 12h8 M8 16h4',
 'M8 4v16 M16 4v16 M4 8h16 M4 16h16 M6 6l12 12',
 'M4 12h15 M14 7l5 5-5 5 M4 4h5 M4 20h5',
 'M3 8h18v13H3z M8 8V3h8v5 M3 13h18 M10 13v3h4v-3',
 'M2 12h4l3-7 6 14 3-7h4',
 'M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6',
 'M4 4h16v6H4z M4 14h16v6H4z M8 7h.01 M8 17h.01 M12 10v4',

 'M12 3a6 6 0 00-4 10.5V17h8v-3.5A6 6 0 0012 3z M9 21h6',
 'M2 12a10 10 0 0120 0 M6 12a6 6 0 0112 0 M10 12a2 2 0 014 0 M12 12v9',
 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z M9 12l2 2 4-4',
 'M5 4h11l3 3v13H5z M9 12l2 2 4-4 M9 17h6',
];
export default function WorkbenchIcon({index}:{index:number}){return <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[index]??paths[0]}/></svg>}
