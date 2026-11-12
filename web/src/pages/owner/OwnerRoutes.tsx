import { Navigate, Route, Routes } from "react-router-dom";

export function OwnerRoutes() {
  return (
    <Routes>
      <Route path="*" element={<Navigate to="/review" replace />} />
    </Routes>
  );
}
