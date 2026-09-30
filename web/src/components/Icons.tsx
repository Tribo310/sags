/** One stroke family for every icon: 24 grid, round caps, currentColor. */
const I = ({ size = 18, w = 2, children }: { size?: number; w?: number; children: React.ReactNode }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={w}
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
);

export const Mic = () => <I><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></I>;
export const Stop = () => <I><rect x="7" y="7" width="10" height="10" rx="1.5" /></I>;
export const Arrow = () => <I size={16}><path d="M5 12h14M13 6l6 6-6 6" /></I>;
export const Check = ({ size = 12 }: { size?: number }) => <I size={size} w={3}><path d="M5 12.5l4.5 4.5L19 7.5" /></I>;
export const Close = () => <I><path d="M6 6l12 12M18 6L6 18" /></I>;
export const Chat = () => <I size={22} w={1.8}><path d="M4 5h16v11H9l-5 4z" /></I>;
export const Basket = () => <I size={22} w={1.8}><path d="M3 9h18l-2 11H5z" /><path d="M8 9l4-5 4 5" /></I>;
export const Route = () => (
  <I size={22} w={1.8}><circle cx="6" cy="18" r="2.5" /><circle cx="18" cy="6" r="2.5" /><path d="M8.5 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5" /></I>
);
export const Home = ({ size = 22 }: { size?: number }) => <I size={size} w={1.8}><path d="M4 11l8-7 8 7v9H4z" /><path d="M10 20v-5h4v5" /></I>;
