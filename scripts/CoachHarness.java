import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.lang.reflect.Array;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.InvocationTargetException;
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
            String methodName = args.length > 0 ? args[0] : "solve";
            String[] keys = args.length > 1 ? args[1].split(",", -1) : new String[] {"__map__"};
            String resultMode = args.length > 2 ? args[2] : "return";
            String resultKey = args.length > 3 ? args[3] : "";
            Method method = findMethod(methodName, keys.length);
            Object[] arguments = new Object[keys.length];
            for (int index = 0; index < keys.length; index++) {
                Object raw = "__map__".equals(keys[index]) ? data : data.get(keys[index]);
                arguments[index] = convert(raw, method.getParameterTypes()[index]);
            }
            Object result = method.invoke(new Solution(), arguments);
            if ("argument".equals(resultMode)) {
                result = arguments[Integer.parseInt(resultKey)];
            } else if ("linked_list".equals(resultMode) && result == null) {
                result = new ArrayList<>();
            } else if ("return_and_prefix".equals(resultMode)) {
                int length = ((Number) result).intValue();
                Map<String, Object> combined = new LinkedHashMap<>();
                combined.put("length", length);
                combined.put("prefix", prefix(arguments[Integer.parseInt(resultKey)], length));
                result = combined;
            }
            System.out.println("{\"result\":" + Json.write(result) + "}");
        } catch (Exception error) {
            Throwable shown = error instanceof InvocationTargetException && error.getCause() != null
                ? error.getCause() : error;
            System.out.println("{\"error\":" + Json.write(shown.getClass().getSimpleName() + ": " + shown.getMessage()) + "}");
        }
    }

    private static Method findMethod(String name, int parameterCount) {
        for (Method method : Solution.class.getDeclaredMethods()) {
            if (method.getName().equals(name) && method.getParameterCount() == parameterCount) {
                method.setAccessible(true);
                return method;
            }
        }
        throw new IllegalArgumentException("Solution 中找不到方法：" + name);
    }

    private static Object convert(Object value, Class<?> target) {
        if (value == null) return null;
        if (target == Map.class) return value;
        if (target == List.class) return value;
        if (target == int.class || target == Integer.class) return ((Number) value).intValue();
        if (target == long.class || target == Long.class) return ((Number) value).longValue();
        if (target == double.class || target == Double.class) return ((Number) value).doubleValue();
        if (target == boolean.class || target == Boolean.class) return value;
        if (target == char.class || target == Character.class) {
            String text = String.valueOf(value);
            if (text.length() != 1) throw new IllegalArgumentException("char 参数必须是单字符字符串");
            return text.charAt(0);
        }
        if (target == String.class) return value;
        if (target.getSimpleName().equals("ListNode")) return linkedList(value, target);
        if (target.getSimpleName().equals("TreeNode")) return binaryTree(value, target);
        if (target == char[].class && value instanceof String) return ((String) value).toCharArray();
        if (target.isArray() && value instanceof List<?>) {
            List<?> list = (List<?>) value;
            Object array = Array.newInstance(target.getComponentType(), list.size());
            for (int index = 0; index < list.size(); index++) {
                Array.set(array, index, convert(list.get(index), target.getComponentType()));
            }
            return array;
        }
        throw new IllegalArgumentException("不支持的 Java 参数类型：" + target.getTypeName());
    }

    private static Object node(Class<?> target, int value) throws ReflectiveOperationException {
        try {
            Constructor<?> constructor = target.getDeclaredConstructor(int.class);
            constructor.setAccessible(true);
            return constructor.newInstance(value);
        } catch (NoSuchMethodException ignored) {
            Constructor<?> constructor = target.getDeclaredConstructor();
            constructor.setAccessible(true);
            Object node = constructor.newInstance();
            field(target, "val").set(node, value);
            return node;
        }
    }

    private static Field field(Class<?> target, String name) throws NoSuchFieldException {
        Class<?> current = target;
        while (current != null) {
            try {
                Field result = current.getDeclaredField(name);
                result.setAccessible(true);
                return result;
            } catch (NoSuchFieldException ignored) {
                current = current.getSuperclass();
            }
        }
        throw new NoSuchFieldException(target.getName() + "." + name);
    }

    private static Object linkedList(Object value, Class<?> target) {
        if (!(value instanceof List<?>)) throw new IllegalArgumentException("ListNode 输入必须是 JSON 数组");
        try {
            Object head = null, tail = null;
            Field next = field(target, "next");
            for (Object raw : (List<?>) value) {
                Object current = node(target, ((Number) raw).intValue());
                if (head == null) head = current;
                else next.set(tail, current);
                tail = current;
            }
            return head;
        } catch (ReflectiveOperationException error) {
            throw new IllegalArgumentException("ListNode 必须包含 val/next 字段和 int 或无参构造器", error);
        }
    }

    private static Object binaryTree(Object value, Class<?> target) {
        if (!(value instanceof List<?>)) throw new IllegalArgumentException("TreeNode 输入必须是层序 JSON 数组");
        List<?> values = (List<?>) value;
        if (values.isEmpty() || values.get(0) == null) return null;
        try {
            Object root = node(target, ((Number) values.get(0)).intValue());
            List<Object> queue = new ArrayList<>();
            queue.add(root);
            Field left = field(target, "left"), right = field(target, "right");
            int input = 1, cursor = 0;
            while (cursor < queue.size() && input < values.size()) {
                Object parent = queue.get(cursor++);
                Object leftValue = values.get(input++);
                if (leftValue != null) {
                    Object child = node(target, ((Number) leftValue).intValue());
                    left.set(parent, child);
                    queue.add(child);
                }
                if (input < values.size()) {
                    Object rightValue = values.get(input++);
                    if (rightValue != null) {
                        Object child = node(target, ((Number) rightValue).intValue());
                        right.set(parent, child);
                        queue.add(child);
                    }
                }
            }
            return root;
        } catch (ReflectiveOperationException error) {
            throw new IllegalArgumentException("TreeNode 必须包含 val/left/right 字段和 int 或无参构造器", error);
        }
    }

    private static List<Object> linkedValues(Object head) {
        List<Object> result = new ArrayList<>();
        try {
            Field value = field(head.getClass(), "val"), next = field(head.getClass(), "next");
            Object current = head;
            while (current != null && result.size() < 10000) {
                result.add(value.get(current));
                current = next.get(current);
            }
            if (current != null) throw new IllegalArgumentException("ListNode 输出疑似有环或过长");
            return result;
        } catch (ReflectiveOperationException error) {
            throw new IllegalArgumentException("无法序列化 ListNode", error);
        }
    }

    private static List<Object> prefix(Object array, int length) {
        List<Object> result = new ArrayList<>();
        for (int index = 0; index < length; index++) result.add(Array.get(array, index));
        return result;
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
            if (value.getClass().getSimpleName().equals("ListNode")) return write(linkedValues(value));
            if (value instanceof List<?>) {
                List<String> items = new ArrayList<>();
                for (Object item : (List<?>) value) items.add(write(item));
                return "[" + String.join(",", items) + "]";
            }
            if (value.getClass().isArray()) {
                List<String> items = new ArrayList<>();
                for (int index = 0; index < Array.getLength(value); index++) items.add(write(Array.get(value, index)));
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

// COACH_LIST_NODE_START
class ListNode {
    int val;
    ListNode next;
    ListNode() {}
    ListNode(int val) { this.val = val; }
}
// COACH_LIST_NODE_END

// COACH_TREE_NODE_START
class TreeNode {
    int val;
    TreeNode left;
    TreeNode right;
    TreeNode() {}
    TreeNode(int val) { this.val = val; }
}
// COACH_TREE_NODE_END
