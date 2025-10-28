import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.net.Socket;
import java.util.Map;
import java.util.Scanner;

@Service
public class FanControlScheduler {

    private final JdbcTemplate jdbcTemplate;
    private static final String PI_HOST = "192.168.43.6"; // 라즈베리파이 IP
    private static final int PI_PORT = 7000;       

    // 동적으로 바뀔 설정값 (나중에 Frontend 연동)
    private volatile int tempThreshold = 40;  // 몇 도 이상일 때 강제 동작?
    private volatile int forcePwm = 100;      // 강제 PWM 값

    public FanControlScheduler(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;

        // 콘솔에서 임계값과 PWM 값을 입력받는 스레드 실행
        new Thread(this::consoleInputLoop, "Console-Input-Thread").start();
    }

    private void consoleInputLoop() {
        Scanner sc = new Scanner(System.in);
        while (true) {
            try {
                System.out.print("[설정] 임계 온도 입력 (현재 " + tempThreshold + "): ");
                tempThreshold = sc.nextInt();

                System.out.print("[설정] 강제 PWM 값 입력 (현재 " + forcePwm + "): ");
                forcePwm = sc.nextInt();

                System.out.println("[설정] 업데이트됨 → tempThreshold=" + tempThreshold + ", forcePwm=" + forcePwm);
            } catch (Exception e) {
                System.out.println("[설정] 입력 오류: " + e.getMessage());
                sc.nextLine(); // 잘못 입력된 값 무시
            }
        }
    }

    private Map<String, Object> getLatestData() {
        String sql = "SELECT cpu_temp, gpu_temp, model_result " +
                     "FROM system_status ORDER BY timestamp DESC LIMIT 1";
        return jdbcTemplate.queryForMap(sql);
    }

    private int calculatePwm(double cpuTemp, double gpuTemp, int modelResult) {
        double f_cpu = cpuTemp / 60.0; // 0~1 정규화
        double f_gpu = gpuTemp / 60.0; // 0~1 정규화
        double f_model = modelResult;  // 0 또는 1

        double pwm = (12 + 88 * Math.max(f_cpu, f_gpu)) * f_model;
        return (int)Math.min(100, Math.round(pwm));
    }

    private void sendToPi(int pwmValue) {
        try (Socket socket = new Socket(PI_HOST, PI_PORT);
             PrintWriter writer = new PrintWriter(new OutputStreamWriter(socket.getOutputStream()), true)) {

            String jsonMessage = String.format("{\"pwm\": %d}\n", pwmValue);
            writer.println(jsonMessage);
            System.out.println("[Spring] 파이로 전송: " + jsonMessage);

        } catch (Exception e) {
            System.err.println("[Spring] 전송 실패: " + e.getMessage());
        }
    }

    @org.springframework.scheduling.annotation.Scheduled(fixedRate = 2000)
    public void controlLoop() {
        try {
            Map<String, Object> row = getLatestData();

            double cpuTemp = ((Number) row.get("cpu_temp")).doubleValue();
            double gpuTemp = ((Number) row.get("gpu_temp")).doubleValue();
            int modelResult = ((Number) row.get("model_result")).intValue();

            int pwm = calculatePwm(cpuTemp, gpuTemp, modelResult);

            // 조건부 강제 제어
            if (cpuTemp >= tempThreshold || gpuTemp >= tempThreshold) {
                pwm = forcePwm;
                System.out.println("[Spring] 임계값 초과 → 강제 PWM 적용: " + forcePwm);
            }

            sendToPi(pwm);

        } catch (Exception e) {
            System.err.println("[Spring] 제어 루프 오류: " + e.getMessage());
        }
    }
}
