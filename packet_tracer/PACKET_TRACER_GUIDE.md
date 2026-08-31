# NetSage AI — Cisco Packet Tracer Topology & Troubleshooting Guide

This guide details the physical layout, CLI configurations, static host setups, verification tests, and NetSage AI troubleshooting scenarios for the **NetSage AI Network Demonstration Topology**.

---

## 1. Network Topology Overview

- **Router**: `R1` (Cisco 1941 / 2911 / 4331 Router)
- **Switch**: `SW1` (Cisco Catalyst 2960 Switch)
- **Server**: `Server-1` (Services & DNS Server)
- **PCs**: `PC-1` (Sales 1), `PC-2` (Sales 2), `PC-3` (Engineering)

### Physical Connections

| Local Device | Local Interface | Connected Device | Remote Interface | Link Type |
| :--- | :--- | :--- | :--- | :--- |
| **R1** | `GigabitEthernet0/0` | **SW1** | `FastEthernet0/24` | 802.1Q Trunk |
| **SW1** | `FastEthernet0/1` | **PC-1** | `FastEthernet0` | Access (VLAN 10) |
| **SW1** | `FastEthernet0/2` | **PC-2** | `FastEthernet0` | Access (VLAN 10) |
| **SW1** | `FastEthernet0/3` | **PC-3** | `FastEthernet0` | Access (VLAN 20) |
| **SW1** | `FastEthernet0/10` | **Server-1** | `FastEthernet0` | Access (VLAN 10) |

---

## 2. IP Addressing & VLAN Matrix

| Device | Interface / Role | IP Address | Subnet Mask | Default Gateway | VLAN |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **R1** | `GigabitEthernet0/0.10` | `192.168.10.1` | `255.255.255.0` | N/A (Gateway) | VLAN 10 (Sales) |
| **R1** | `GigabitEthernet0/0.20` | `192.168.20.1` | `255.255.255.0` | N/A (Gateway) | VLAN 20 (Engineering) |
| **PC-1** | `FastEthernet0` | `192.168.10.10` | `255.255.255.0` | `192.168.10.1` | VLAN 10 |
| **PC-2** | `FastEthernet0` | `192.168.10.11` | `255.255.255.0` | `192.168.10.1` | VLAN 10 |
| **PC-3** | `FastEthernet0` | `192.168.20.10` | `255.255.255.0` | `192.168.20.1` | VLAN 20 |
| **Server-1** | `FastEthernet0` | `192.168.10.254` | `255.255.255.0` | `192.168.10.1` | VLAN 10 |

---

## 3. Cisco CLI Initialization Scripts

### Router (R1) Configuration Script

> **Note**: If prompted *"Would you like to enter the initial configuration dialog? [yes/no]:"*, type **`no`** and press Enter.

```ciscoswitch
enable
configure terminal
hostname R1

interface GigabitEthernet0/0
 no shutdown
 exit

interface GigabitEthernet0/0.10
 encapsulation dot1Q 10
 ip address 192.168.10.1 255.255.255.0
 exit

interface GigabitEthernet0/0.20
 encapsulation dot1Q 20
 ip address 192.168.20.1 255.255.255.0
 exit

end
write memory
```

### Switch (SW1) Configuration Script

```ciscoswitch
enable
configure terminal
hostname SW1

vlan 10
 name Sales
 exit

vlan 20
 name Engineering
 exit

interface FastEthernet0/1
 switchport mode access
 switchport access vlan 10
 no shutdown
 exit

interface FastEthernet0/2
 switchport mode access
 switchport access vlan 10
 no shutdown
 exit

interface FastEthernet0/3
 switchport mode access
 switchport access vlan 20
 no shutdown
 exit

interface FastEthernet0/10
 switchport mode access
 switchport access vlan 10
 no shutdown
 exit

interface FastEthernet0/24
 switchport mode trunk
 no shutdown
 exit

end
write memory
```

---

## 4. The 3 Final Completion Steps

### Step 1: Configure IP Addresses on PCs and Server

Open each device in Cisco Packet Tracer → **Desktop** → **IP Configuration** → Select **Static**:

1. **PC-1 (Sales 1)**:
   - IP Address: `192.168.10.10`
   - Subnet Mask: `255.255.255.0`
   - Default Gateway: `192.168.10.1`
2. **PC-2 (Sales 2)**:
   - IP Address: `192.168.10.11`
   - Subnet Mask: `255.255.255.0`
   - Default Gateway: `192.168.10.1`
3. **PC-3 (Engineering)**:
   - IP Address: `192.168.20.10`
   - Subnet Mask: `255.255.255.0`
   - Default Gateway: `192.168.20.1`
4. **Server-1 (Services)**:
   - IP Address: `192.168.10.254`
   - Subnet Mask: `255.255.255.0`
   - Default Gateway: `192.168.10.1`

---

### Step 2: Verify Inter-VLAN Network Connectivity

1. Click **PC-1** → **Desktop** → **Command Prompt**.
2. Execute local gateway ping:
   ```cmd
   ping 192.168.10.1
   ```
3. Execute inter-VLAN ping to **PC-3**:
   ```cmd
   ping 192.168.20.10
   ```
4. Both pings should return `Reply from ...`, proving that **Router-on-a-Stick inter-VLAN routing** is functioning properly.

---

### Step 3: Save genuine `.pkt` Project File

1. In Packet Tracer menu, click **File** → **Save As...**
2. Save the project inside your local `packet_tracer/` directory as:
   `packet_tracer/NetSage_AI_Network_Topology.pkt`

---

## 5. Reproducing NetSage AI Troubleshooting Scenarios

### Case 1 — VLAN Mismatch
- **Fault Injection**: On `SW1 CLI`, change `Fa0/1` to VLAN 20:
  ```ciscoswitch
  configure terminal
  interface FastEthernet0/1
   switchport access vlan 20
  ```
- **Symptom**: `PC-1` (IP `192.168.10.10`) loses connectivity to gateway `192.168.10.1`.
- **Verification Command**: `show interface FastEthernet0/1 switchport` or `show vlan brief`.

### Case 8 — Duplicate IP Address
- **Fault Injection**: On `PC-2`, set IP Address to `192.168.10.10` (same as `PC-1`).
- **Symptom**: IP address conflict, ARP table flapping, and intermittent ping drops.
- **Verification Command**: `show ip arp` on `R1` or `arp -a` on `PC-1`.

### Case 10 — Gateway Subnet Mismatch
- **Fault Injection**: On `PC-1`, set Default Gateway to `192.168.20.1` (VLAN 20 gateway).
- **Symptom**: `PC-1` cannot reach external subnets or servers because default gateway is outside local broadcast domain.
- **Verification Command**: `ipconfig /all` on `PC-1`.

---

## 6. Restoring Clean Network State

- **SW1 CLI**:
  ```ciscoswitch
  configure terminal
  interface FastEthernet0/1
   switchport access vlan 10
  ```
- **PC-1**: IP = `192.168.10.10`, Gateway = `192.168.10.1`
- **PC-2**: IP = `192.168.10.11`, Gateway = `192.168.10.1`
- **PC-3**: IP = `192.168.20.10`, Gateway = `192.168.20.1`
