import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Small JSON bridge for offline algorithm practice. No external libraries. */
public final class CoachHarness {
    private CoachHarness() {}

    public static void main(String[] args) throws Exception {
        String input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8)).readLine();
        try {
            Object parsed = new Json(input).parse();
            if (!(parsed instanceof Map)) throw new IllegalArgumentException("输入必须是 JSON 对象");
            @SuppressWarnings("unchecked")
            Map<String, Object> data = (Map<String, Object>) parsed;
            Object result = Solution.solve(data);
            System.out.println("{\"result\":" + Json.write(result) + "}");
        } catch (Exception error) {
            System.out.println("{\"error\":" + Json.write(error.getClass().getSimpleName() + ": " + error.getMessage()) + "}");
        }
    }

    static final class Json {
        private final String source;
        private int position;

        Json(String source) { this.source = source == null ? "" : source; }

        Object parse() {
            Object value = value();
            space();
            if (position != source.length()) throw new IllegalArgumentException("JSON 尾部有多余字符");
            return value;
        }

        private void space() {
            while (position < source.length() && Character.isWhitespace(source.charAt(position))) position++;
        }

        private char take() {
            if (position >= source.length()) throw new IllegalArgumentException("JSON 提前结束");
            return source.charAt(position++);
        }

        private void expect(char wanted) {
            if (take() != wanted) throw new IllegalArgumentException("JSON 缺少 " + wanted);
        }

        private Object value() {
            space();
            char current = take();
            if (current == '"') return string();
            if (current == '{') {
                Map<String, Object> object = new LinkedHashMap<>();
                space();
                if (source.charAt(position) == '}') { position++; return object; }
                while (true) {
                    space();
                    expect('"');
                    String key = string();
                    space();
                    expect(':');
                    object.put(key, value());
                    space();
                    char next = take();
                    if (next == '}') return object;
                    if (next != ',') throw new IllegalArgumentException("JSON 对象分隔符错误");
                }
            }
            if (current == '[') {
                List<Object> array = new ArrayList<>();
                space();
                if (source.charAt(position) == ']') { position++; return array; }
                while (true) {
                    array.add(value());
                    space();
                    char next = take();
                    if (next == ']') return array;
                    if (next != ',') throw new IllegalArgumentException("JSON 数组分隔符错误");
                }
            }
            position--;
            if (source.startsWith("true", position)) { position += 4; return true; }
            if (source.startsWith("false", position)) { position += 5; return false; }
            if (source.startsWith("null", position)) { position += 4; return null; }
            int start = position;
            while (position < source.length() && "-+0123456789.eE".indexOf(source.charAt(position)) >= 0) position++;
            if (position == start) throw new IllegalArgumentException("无效 JSON 值");
            String number = source.substring(start, position);
            if (number.contains(".") || number.contains("e") || number.contains("E")) return Double.parseDouble(number);
            return Long.parseLong(number);
        }

        private String string() {
            StringBuilder result = new StringBuilder();
            while (true) {
                char current = take();
                if (current == '"') return result.toString();
                if (current == '\\') {
                    char escaped = take();
                    switch (escaped) {
                        case '"': case '\\': case '/': result.append(escaped); break;
                        case 'b': result.append('\b'); break;
                        case 'f': result.append('\f'); break;
                        case 'n': result.append('\n'); break;
                        case 'r': result.append('\r'); break;
                        case 't': result.append('\t'); break;
                        case 'u':
                            if (position + 4 > source.length()) throw new IllegalArgumentException("Unicode 转义不完整");
                            result.append((char) Integer.parseInt(source.substring(position, position + 4), 16));
                            position += 4;
                            break;
                        default: throw new IllegalArgumentException("无效转义");
                    }
                } else {
                    if (current < 0x20) throw new IllegalArgumentException("字符串含控制字符");
                    result.append(current);
                }
            }
        }

        static String write(Object value) {
            if (value == null) return "null";
            if (value instanceof Boolean || value instanceof Number) return value.toString();
            if (value instanceof String) {
                StringBuilder result = new StringBuilder("\"");
                for (char current : ((String) value).toCharArray()) {
                    switch (current) {
                        case '"': result.append("\\\""); break;
                        case '\\': result.append("\\\\"); break;
                        case '\n': result.append("\\n"); break;
                        case '\r': result.append("\\r"); break;
                        case '\t': result.append("\\t"); break;
                        default:
                            if (current < 0x20) result.append(String.format("\\u%04x", (int) current));
                            else result.append(current);
                    }
                }
                return result.append('"').toString();
            }
            if (value instanceof List<?>) {
                List<String> items = new ArrayList<>();
                for (Object item : (List<?>) value) items.add(write(item));
                return "[" + String.join(",", items) + "]";
            }
            if (value instanceof Map<?, ?>) {
                List<String> items = new ArrayList<>();
                for (Map.Entry<?, ?> item : ((Map<?, ?>) value).entrySet()) {
                    if (!(item.getKey() instanceof String)) throw new IllegalArgumentException("JSON 键必须是字符串");
                    items.add(write(item.getKey()) + ":" + write(item.getValue()));
                }
                return "{" + String.join(",", items) + "}";
            }
            throw new IllegalArgumentException("返回值必须是 JSON 可序列化类型");
        }
    }
}
