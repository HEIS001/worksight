-- Schema for the Worksight database

CREATE TABLE users (
    user_id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(50) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE staff (
    staff_id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    position VARCHAR(50),
    hire_date DATE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    INDEX (position)
);

CREATE TABLE checkins (
    checkin_id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    checkin_time DATETIME NOT NULL,
    checkout_time DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    INDEX (checkin_time)
);