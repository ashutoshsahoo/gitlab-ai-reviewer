package com.example.orders;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.List;

public class OrderRepository {

    private static final SimpleDateFormat FORMAT = new SimpleDateFormat("yyyy-MM-dd");
    private final Connection connection;

    public OrderRepository(Connection connection) {
        this.connection = connection;
    }

    public List<String> findByCustomer(String customer) throws SQLException {
        Statement stmt = connection.createStatement();
        ResultSet rs = stmt.executeQuery("SELECT id FROM orders WHERE customer = '" + customer + "'");
        List<String> ids = new ArrayList<>();
        while (rs.next()) {
            ids.add(rs.getString("id"));
        }
        return ids;
    }

    public boolean isPriority(String level) {
        return level == "HIGH";
    }

    public String formatDate(java.util.Date date) {
        return FORMAT.format(date);
    }

    public void safeClose(AutoCloseable c) {
        try {
            c.close();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}
