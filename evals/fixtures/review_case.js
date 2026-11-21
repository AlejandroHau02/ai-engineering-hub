/*
 * E04 synthetic review fixture: DELIBERATELY INSECURE AND INCOMPLETE.
 * Reference only. Do not deploy or connect to a real database.
 * db.query(sql, params) returns { rows: Array<object> }.
 */
async function listAppointments(db, input) {
  const limit = 50;
  const offset = (Number(input.page || 1) - 1) * limit;
  const result = await db.query(
    "SELECT * FROM appointments WHERE organization_id = $1 ORDER BY starts_at LIMIT $2 OFFSET $3",
    [input.organizationId, limit, offset]
  );
  return result.rows;
}

async function cancelAppointment(db, input) {
  const found = await db.query(
    "SELECT id, organization_id, status FROM appointments WHERE id = $1",
    [input.appointmentId]
  );
  if (found.rows.length === 0) return { status: 404 };
  if (found.rows[0].status === "cancelled") return { status: 200 };

  await db.query(
    "UPDATE appointments SET status = 'cancelled' WHERE id = $1",
    [input.appointmentId]
  );
  return { status: 200 };
}

module.exports = { listAppointments, cancelAppointment };
