export default function MemoryPage() {
    return (
        <div className="text-center py-12">
            <div className="text-secondary-400 dark:text-secondary-600 mb-4">
                <svg
                    className="mx-auto h-12 w-12"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                >
                    <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"
                    />
                </svg>
            </div>
            <h3 className="text-lg font-medium text-navy-900 dark:text-navy-100 mb-1">
                Memory
            </h3>
            <p className="text-secondary-500 dark:text-secondary-400">
                Memory management coming soon...
            </p>
        </div>
    );
}
